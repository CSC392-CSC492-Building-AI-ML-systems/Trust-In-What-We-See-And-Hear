"""The current user's identity verification. See service.py for a session's life.

AI-assisted code (written with Claude Code), cited per the CSC398 AI policy.
"""

import logging
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request, status

from app.core.auth import AuthenticatedUser, get_current_user
from app.core.errors import api_error, error_responses, merge_responses
from app.integrations.identity_verification import (
    DiditClient,
    IdentityVerificationError,
    get_didit_client,
    optional_didit_client,
)
from app.integrations.supabase import SupabaseDatabase, get_supabase_db
from app.modules.verification.schemas import (
    VerificationStartResponse,
    VerificationStateResponse,
    VerificationSummary,
)
from app.modules.verification.service import (
    SETTLED,
    latest_verification,
    record_session,
    sync,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/users/me/verification", tags=["Identity verification"])

CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_user)]
SupabaseDb = Annotated[SupabaseDatabase, Depends(get_supabase_db)]

PROFILE_NOT_FOUND = ("profile_not_found", "The user has not finished onboarding.")
UNAVAILABLE = (
    "verification_unavailable",
    "Identity verification is not configured, or the provider is unreachable. Retry later.",
)


@router.post(
    "",
    response_model=VerificationStartResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses=merge_responses(
        error_responses(status.HTTP_404_NOT_FOUND, PROFILE_NOT_FOUND),
        error_responses(
            status.HTTP_409_CONFLICT,
            ("already_verified", "The user's identity is already verified."),
            ("verification_in_review", "The latest session is being reviewed; wait for it."),
        ),
        error_responses(status.HTTP_503_SERVICE_UNAVAILABLE, UNAVAILABLE),
    ),
)
async def start_verification(
    request: Request,
    user: CurrentUser,
    supabase_db: SupabaseDb,
    didit: Annotated[DiditClient, Depends(get_didit_client)],
) -> VerificationStartResponse:
    """Start identity verification, or carry on with an unfinished one.

    Returns the provider's page where the user verifies (an ID document, a
    selfie and a liveness check). Calling this again before the user finishes
    returns the same session. An unfinished session's status is checked with
    the provider first, so a session the user already passed gives
    `409 already_verified`. The result arrives later: poll
    `GET /users/me/verification`.

    Clients cannot mark themselves verified; only the provider's result does.
    """
    profile = await _profile(supabase_db, user.user_id)
    verified = profile["verified_at"] is not None
    latest = None
    if not verified:
        latest = await latest_verification(supabase_db, user.user_id)
    settings = request.app.state.settings
    try:
        if latest is not None and latest["status"] not in SETTLED:
            # The stored status may be behind (the user can finish on Didit
            # without GET being called), and Didit never reuses an approved or
            # in-review session, so check it before deciding.
            latest, now_verified = await sync(supabase_db, didit, latest)
            verified = verified or now_verified
        if verified:
            api_error(
                status.HTTP_409_CONFLICT, "already_verified", "Your identity is already verified."
            )
        if latest is not None and latest["status"] == "in_review":
            api_error(
                status.HTTP_409_CONFLICT,
                "verification_in_review",
                "Your verification is being reviewed. You will be able to retry if it is declined.",
            )
        session = await didit.create_session(
            settings.didit_workflow_id,
            vendor_data=str(user.user_id),
            callback=settings.identity_verification_return_url,
        )
        row = await record_session(supabase_db, user.user_id, session)
    except IdentityVerificationError as exc:
        logger.error("Could not start a verification session: %s", exc)
        api_error(status.HTTP_503_SERVICE_UNAVAILABLE, *UNAVAILABLE)
    return VerificationStartResponse(
        verification_id=row["id"], status=row["status"], url=session.url
    )


@router.get(
    "",
    response_model=VerificationStateResponse,
    responses=error_responses(status.HTTP_404_NOT_FOUND, PROFILE_NOT_FOUND),
)
async def get_verification(
    user: CurrentUser,
    supabase_db: SupabaseDb,
    didit: Annotated[DiditClient | None, Depends(optional_didit_client)],
) -> VerificationStateResponse:
    """Whether the user is verified, and their latest verification session.

    While the session is unsettled, its status is checked with the provider
    first, so polling this after the user returns from the provider's page
    shows the result.
    """
    profile = await _profile(supabase_db, user.user_id)
    verified = profile["verified_at"] is not None
    row = await latest_verification(supabase_db, user.user_id)
    if row is not None and row["status"] not in SETTLED and didit is not None:
        try:
            row, now_verified = await sync(supabase_db, didit, row)
        except IdentityVerificationError as exc:
            # The stored status is still right, only possibly behind.
            logger.warning("Could not check verification %s: %s", row["id"], exc)
        else:
            verified = verified or now_verified
    return VerificationStateResponse(
        verified=verified,
        verification=VerificationSummary(**_summary_fields(row)) if row else None,
    )


async def _profile(supabase_db: SupabaseDatabase, user_id: UUID) -> dict[str, Any]:
    rows = await supabase_db.select("users", {"id": f"eq.{user_id}"}, "id,verified_at")
    if not rows:
        api_error(
            status.HTTP_404_NOT_FOUND,
            PROFILE_NOT_FOUND[0],
            "No profile yet. Finish onboarding first.",
        )
    return rows[0]


def _summary_fields(row: dict[str, Any]) -> dict[str, Any]:
    return {key: row[key] for key in ("id", "status", "created_at", "updated_at")}
