"""Routes for the voice pipeline only (voice-pipeline/BACKEND.md sections 2 and 3).

They take the pipeline's shared secret instead of a user's token, and never
appear in a user's flow. See service.py for a call's life.

AI-assisted code (written with Claude Code), cited per the CSC398 AI policy.
"""

import hmac
import logging
from typing import Annotated, Any, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import Settings
from app.core.errors import api_error, error_responses, merge_responses, unauthorized
from app.integrations.supabase import (
    SupabaseConflictError,
    SupabaseDatabase,
    SupabaseStorage,
    get_supabase_db,
    get_supabase_storage,
)
from app.modules.calls.schemas import CallClaim, CallReport, CallSetupResponse
from app.modules.calls.service import (
    ENDED_BY_ERROR,
    FINISHED_STATUSES,
    NOT_SCORED,
    VOICE_UNAVAILABLE,
    hash_claim_token,
    pipeline_secret,
)
from app.modules.consents.service import DATABASE_NOW, USABLE_VOICE_STATUSES

logger = logging.getLogger(__name__)

pipeline_scheme = HTTPBearer(
    scheme_name="PipelineSecret",
    description="The shared secret the voice pipeline holds (`PIPELINE_BACKEND_SECRET`).",
    auto_error=False,
)


async def require_pipeline(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Security(pipeline_scheme)],
) -> None:
    secret = pipeline_secret(request.app.state.settings)
    if not secret:
        api_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "calls_unavailable",
            "The pipeline secret is not configured.",
        )
    if credentials is None or not hmac.compare_digest(
        credentials.credentials.encode(), secret.encode()
    ):
        unauthorized("Invalid pipeline secret.")


router = APIRouter(
    prefix="/internal/calls",
    tags=["Internal"],
    dependencies=[Depends(require_pipeline)],
    responses={status.HTTP_401_UNAUTHORIZED: {"description": "Wrong or missing secret."}},
)

SupabaseDb = Annotated[SupabaseDatabase, Depends(get_supabase_db)]
Storage = Annotated[SupabaseStorage, Depends(get_supabase_storage)]

CALL_NOT_FOUND = ("call_not_found", "No call has this ID.")


@router.post(
    "/{id}/claim",
    response_model=CallSetupResponse,
    responses=merge_responses(
        error_responses(
            status.HTTP_403_FORBIDDEN,
            ("token_rejected", "The token is wrong, expired or already used."),
        ),
        error_responses(status.HTTP_404_NOT_FOUND, CALL_NOT_FOUND),
        error_responses(
            status.HTTP_409_CONFLICT,
            ("voice_unavailable", "The persona's voice is gone; the call is now `failed`."),
        ),
    ),
)
async def claim_call(
    id: UUID,
    payload: CallClaim,
    request: Request,
    supabase_db: SupabaseDb,
    storage: Storage,
) -> CallSetupResponse:
    """Accept the browser's token, start the call, and hand over its setup.

    The token works once: the call moves from `queued` to `in_progress` in the
    same conditional update that checks it.
    """
    settings = request.app.state.settings
    calls = await supabase_db.select(
        "calls",
        {"id": f"eq.{id}"},
        "id,persona_user_id,call_context,max_duration_seconds",
    )
    if not calls:
        api_error(status.HTTP_404_NOT_FOUND, *CALL_NOT_FOUND)
    call = calls[0]

    claimed = await supabase_db.update(
        "calls",
        {
            "id": f"eq.{id}",
            "status": "eq.queued",
            "claim_token_hash": f"eq.{hash_claim_token(payload.token)}",
            "claim_expires_at": f"gt.{DATABASE_NOW}",
        },
        # The context was read above; it is not kept once the pipeline has it.
        {
            "status": "in_progress",
            "started_at": DATABASE_NOW,
            "claim_token_hash": None,
            "call_context": None,
        },
        "id",
    )
    if not claimed:
        api_error(
            status.HTTP_403_FORBIDDEN,
            "token_rejected",
            "The token is wrong, expired or already used.",
        )

    try:
        voice_url = await _voice_url(call, supabase_db, storage, settings)
    except Exception:
        await _fail(supabase_db, id, VOICE_UNAVAILABLE)
        raise
    if voice_url is None or not isinstance(call["call_context"], dict):
        await _fail(supabase_db, id, VOICE_UNAVAILABLE)
        api_error(
            status.HTTP_409_CONFLICT,
            "voice_unavailable",
            "The persona's voice is no longer available.",
        )
    return CallSetupResponse(
        context=call["call_context"],
        voice_url=voice_url,
        max_duration_seconds=call["max_duration_seconds"],
    )


@router.post(
    "/{id}/result",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=merge_responses(
        error_responses(status.HTTP_404_NOT_FOUND, CALL_NOT_FOUND),
        error_responses(
            status.HTTP_409_CONFLICT,
            ("call_not_in_progress", "The call was never claimed."),
        ),
    ),
)
async def report_call_result(id: UUID, payload: CallReport, supabase_db: SupabaseDb) -> Response:
    """Store how a claimed call ended and its result.

    The pipeline may send the same report more than once; a call that is
    already finished answers `204` and is left unchanged.
    """
    rows = await supabase_db.select("calls", {"id": f"eq.{id}"}, "id,status")
    if not rows:
        api_error(status.HTTP_404_NOT_FOUND, *CALL_NOT_FOUND)
    if rows[0]["status"] in FINISHED_STATUSES:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    if rows[0]["status"] != "in_progress":
        _not_in_progress()

    completed = payload.result is not None and payload.ended_by != "error"
    if completed:
        try:
            await supabase_db.insert(
                "call_results",
                {"call_id": str(id), **payload.result.model_dump()},
                "call_id",
            )
        except SupabaseConflictError:
            pass  # stored by an earlier attempt whose answer was lost
    failure_reason = None
    if not completed:
        failure_reason = ENDED_BY_ERROR if payload.ended_by == "error" else NOT_SCORED
    updated = await supabase_db.update(
        "calls",
        {"id": f"eq.{id}", "status": "eq.in_progress"},
        {
            "status": "completed" if completed else "failed",
            "failure_reason": failure_reason,
            "ended_at": DATABASE_NOW,
            "duration_seconds": payload.duration_seconds,
            "call_context": None,
        },
        "id",
    )
    if not updated:
        # A concurrent report finished it first, or it timed out meanwhile.
        logger.info("Call %s was finished before its result was stored.", id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _voice_url(
    call: dict[str, Any],
    supabase_db: SupabaseDatabase,
    storage: SupabaseStorage,
    settings: Settings,
) -> str | None:
    """A signed link to the persona's sample, or None if their voice is gone."""
    persona_id = call["persona_user_id"]
    if persona_id is None:  # the persona deleted their account
        return None
    voices = await supabase_db.select(
        "voices", {"user_id": f"eq.{persona_id}"}, "status,sample_storage_path"
    )
    if (
        not voices
        or voices[0]["status"] not in USABLE_VOICE_STATUSES
        or not voices[0]["sample_storage_path"]
    ):
        return None
    link = await storage.signed_url(
        settings.voice_samples_bucket,
        voices[0]["sample_storage_path"],
        settings.call_voice_url_valid_seconds,
    )
    return pipeline_link(link, settings.pipeline_supabase_url)


def pipeline_link(link: str, pipeline_supabase_url: str | None) -> str:
    """The Storage link with Supabase's address as the pipeline reaches it."""
    if not pipeline_supabase_url:
        return link
    path = link.find("/storage/v1/")
    if path < 0:
        return link
    return pipeline_supabase_url.rstrip("/") + link[path:]


async def _fail(supabase_db: SupabaseDatabase, call_id: UUID, reason: str) -> None:
    await supabase_db.update(
        "calls",
        {"id": f"eq.{call_id}", "status": "eq.in_progress"},
        {"status": "failed", "failure_reason": reason, "ended_at": DATABASE_NOW},
        "id",
    )


def _not_in_progress() -> NoReturn:
    api_error(status.HTTP_409_CONFLICT, "call_not_in_progress", "The call was never claimed.")
