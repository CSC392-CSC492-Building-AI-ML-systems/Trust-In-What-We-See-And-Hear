"""The current user's calls. See service.py for a call's life.

AI-assisted code (written with Claude Code), cited per the CSC398 AI policy.
"""

from datetime import timedelta
from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status

from app.api.schemas import Page, Pagination
from app.core import clock
from app.core.auth import AuthenticatedUser, get_current_user
from app.core.errors import api_error, error_responses, merge_responses
from app.integrations.supabase import SupabaseDatabase, get_supabase_db
from app.modules.calls.schemas import (
    CallCreate,
    CallDetailResponse,
    CallResponse,
    CallStartResponse,
)
from app.modules.calls.service import (
    CALL_COLUMNS,
    CALL_CONSENT_COLUMNS,
    CallContextError,
    build_context,
    call_fields,
    fail_stale_calls,
    full_name,
    new_claim_token,
    pipeline_secret,
    to_call,
    to_summary,
)
from app.modules.consents.service import (
    ConsentNotUsableError,
    restore_consent,
    usable_filters,
    use_consent,
)
from app.modules.scenarios.router import visible_to

router = APIRouter(prefix="/calls", tags=["Calls"])

CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_user)]
SupabaseDb = Annotated[SupabaseDatabase, Depends(get_supabase_db)]

SCENARIO_COLUMNS = "id,title,description,ask,red_flags,max_duration_seconds"
CONSENT_UNUSABLE = (
    "consent_unusable",
    "The consent does not exist, is not yours, is not accepted, was already used or expired.",
)


@router.post(
    "",
    response_model=CallStartResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses=merge_responses(
        error_responses(
            status.HTTP_404_NOT_FOUND,
            ("scenario_not_found", "No scenario the current user can see has this ID."),
        ),
        error_responses(
            status.HTTP_409_CONFLICT,
            CONSENT_UNUSABLE,
            ("voice_not_ready", "The friend's voice is not ready to use."),
            ("scenario_incomplete", "The scenario has no `ask`; set one first."),
            ("call_context_invalid", "The scenario or profiles cannot be used for a call."),
        ),
        error_responses(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            ("calls_unavailable", "The voice pipeline is not configured on the server."),
        ),
    ),
)
async def start_call(
    payload: CallCreate, request: Request, user: CurrentUser, supabase_db: SupabaseDb
) -> CallStartResponse:
    """Start a scam call to the current user, using up the consent.

    Connect to the returned `ws_url` within a couple of minutes to take the
    call (voice-pipeline/PROTOCOL.md). After it ends, poll `GET /calls/{id}`
    until `status` is `completed` or `failed`.

    The consent is only marked used after the scenario, the consent and the
    call context have been checked, so those errors leave it usable.
    """
    settings = request.app.state.settings
    if not settings.pipeline_ws_url or not pipeline_secret(settings):
        api_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "calls_unavailable",
            "Calls are not available right now.",
        )

    scenarios = await supabase_db.select(
        "scam_scenarios",
        {**visible_to(user.user_id), "id": f"eq.{payload.scenario_id}"},
        SCENARIO_COLUMNS,
    )
    if not scenarios:
        api_error(status.HTTP_404_NOT_FOUND, "scenario_not_found", "Scenario not found.")
    scenario = scenarios[0]

    # Read first, so the context is checked before the consent is used up.
    consents = await supabase_db.select(
        "scam_consents",
        {"id": f"eq.{payload.consent_id}", **usable_filters(user.user_id)},
        CALL_CONSENT_COLUMNS,
    )
    if not consents:
        api_error(status.HTTP_409_CONFLICT, *CONSENT_UNUSABLE)
    consent = consents[0]
    try:
        context = build_context(consent, scenario)
    except CallContextError as exc:
        api_error(status.HTTP_409_CONFLICT, exc.code, exc.message)

    try:
        # Checks again, atomically, and fails if it was used meanwhile.
        await use_consent(supabase_db, payload.consent_id, user.user_id)
    except ConsentNotUsableError as exc:
        api_error(status.HTTP_409_CONFLICT, exc.code, exc.message)

    token, token_hash = new_claim_token()
    claim_valid = timedelta(seconds=settings.call_claim_valid_seconds)
    try:
        created = await supabase_db.insert(
            "calls",
            {
                "user_id": str(user.user_id),
                "persona_user_id": consent["consenter_id"],
                "consent_id": str(payload.consent_id),
                "scenario_id": scenario["id"],
                "persona_name": full_name(consent["consenter"]),
                "scenario_title": scenario["title"],
                "status": "queued",
                "claim_token_hash": token_hash,
                "claim_expires_at": clock.to_iso(clock.now() + claim_valid),
                "call_context": context.to_dict(),
                "max_duration_seconds": scenario["max_duration_seconds"],
            },
            CALL_COLUMNS,
        )
    except Exception:
        # The consent was used up for this call, which does not exist, so give
        # it back. If the insert did succeed and only its answer was lost, the
        # consent can start one more call; a call nobody connects to times out.
        await restore_consent(supabase_db, payload.consent_id, user.user_id)
        raise
    base = settings.pipeline_ws_url.rstrip("/")
    ws_url = f"{base}/ws/calls/{created['id']}?token={quote(token, safe='')}"
    return CallStartResponse(**call_fields(created), ws_url=ws_url)


@router.get("", response_model=Page[CallResponse])
async def get_call_history(
    pagination: Annotated[Pagination, Query()],
    request: Request,
    user: CurrentUser,
    supabase_db: SupabaseDb,
) -> Page[CallResponse]:
    """List the current user's calls, newest first."""
    await fail_stale_calls(
        supabase_db, user.user_id, request.app.state.settings.call_result_timeout_seconds
    )
    rows = await supabase_db.select(
        "calls",
        {"user_id": f"eq.{user.user_id}"},
        CALL_COLUMNS,
        order="created_at.desc,id",
        limit=pagination.limit,
        offset=pagination.offset,
    )
    return Page(
        items=[to_call(row) for row in rows],
        limit=pagination.limit,
        offset=pagination.offset,
    )


@router.get(
    "/{id}",
    response_model=CallDetailResponse,
    responses=error_responses(
        status.HTTP_404_NOT_FOUND,
        ("call_not_found", "The current user has no call with this ID."),
    ),
)
async def get_call_details(
    id: UUID, request: Request, user: CurrentUser, supabase_db: SupabaseDb
) -> CallDetailResponse:
    """Return a call's details, including its result once it is `completed`."""
    await fail_stale_calls(
        supabase_db, user.user_id, request.app.state.settings.call_result_timeout_seconds
    )
    rows = await supabase_db.select(
        "calls", {"id": f"eq.{id}", "user_id": f"eq.{user.user_id}"}, CALL_COLUMNS
    )
    if not rows:
        api_error(status.HTTP_404_NOT_FOUND, "call_not_found", "Call not found.")
    row = rows[0]
    return CallDetailResponse(
        **call_fields(row),
        summary=to_summary(row),
        failure_reason=row["failure_reason"] if row["status"] == "failed" else None,
    )

