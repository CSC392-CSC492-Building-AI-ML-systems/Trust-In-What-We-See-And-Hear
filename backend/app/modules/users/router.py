from datetime import UTC, datetime
from typing import Annotated, Any, Literal, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import ValidationError

from app.core.auth import AuthenticatedUser, get_current_user
from app.core.errors import NOT_FOUND_RESPONSES, Responses, database_unavailable
from app.integrations.supabase import (
    SupabaseConflictError,
    SupabaseDatabase,
    SupabaseUnavailableError,
    get_supabase_db,
)
from app.modules.users.schemas import (
    ConflictDetail,
    ConflictResponse,
    OnboardingRequest,
    UpdateProfileRequest,
    UserProfileResponse,
)

router = APIRouter(prefix="/users/me", tags=["Users"])

CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_user)]
SupabaseDb = Annotated[SupabaseDatabase, Depends(get_supabase_db)]

PROFILE_COLUMNS = (
    "id,username,first_name,last_name,preferred_language,verified_at,onboarding_answers,created_at"
)
# Default Postgres names for the primary key and the unique username constraint.
PROFILE_EXISTS_CONSTRAINT = "users_pkey"
USERNAME_TAKEN_CONSTRAINT = "users_username_key"

USERNAME_TAKEN_RESPONSES: Responses = {
    status.HTTP_409_CONFLICT: {
        "model": ConflictResponse,
        "description": "The username is taken (`detail.code = \"username_taken\"`).",
    }
}
ONBOARDING_CONFLICT_RESPONSES: Responses = {
    status.HTTP_409_CONFLICT: {
        "model": ConflictResponse,
        "description": (
            "The user has already finished onboarding (`profile_exists`), or the "
            "username is taken (`username_taken`)."
        ),
    }
}
PROFILE_NOT_FOUND_RESPONSES: Responses = {
    status.HTTP_404_NOT_FOUND: {
        **NOT_FOUND_RESPONSES[status.HTTP_404_NOT_FOUND],
        "description": "The user has not finished onboarding, so has no profile yet.",
    }
}


@router.get("", response_model=UserProfileResponse, responses=PROFILE_NOT_FOUND_RESPONSES)
async def get_user_info(user: CurrentUser, supabase_db: SupabaseDb) -> UserProfileResponse:
    """Return the current user's profile.

    Returns `404` if the user has not finished onboarding, because the profile
    is only created then.
    """
    try:
        rows = await supabase_db.select("users", {"id": f"eq.{user.user_id}"}, PROFILE_COLUMNS)
    except SupabaseUnavailableError:
        database_unavailable()
    if not rows:
        _profile_not_found()
    return _to_profile(rows[0])


@router.patch(
    "",
    response_model=UserProfileResponse,
    responses=PROFILE_NOT_FOUND_RESPONSES | USERNAME_TAKEN_RESPONSES,
)
async def edit_user_info(
    payload: UpdateProfileRequest, user: CurrentUser, supabase_db: SupabaseDb
) -> UserProfileResponse:
    """Update the current user's profile.

    Only the fields that are sent are changed. Send `"onboarding_answers": null`
    to remove the stored answers; the user is not sent back through onboarding.
    """
    values = payload.model_dump(exclude_unset=True)
    if not values:
        return await get_user_info(user, supabase_db)
    filters = {"id": f"eq.{user.user_id}"}
    try:
        rows = await supabase_db.update("users", filters, values, PROFILE_COLUMNS)
    except SupabaseUnavailableError:
        database_unavailable()
    except SupabaseConflictError as exc:
        if exc.constraint == USERNAME_TAKEN_CONSTRAINT:
            _username_taken()
        raise
    if not rows:
        _profile_not_found()
    return _to_profile(rows[0])


@router.post(
    "/onboarding",
    response_model=UserProfileResponse,
    status_code=status.HTTP_201_CREATED,
    responses=ONBOARDING_CONFLICT_RESPONSES,
)
async def finish_onboarding(
    payload: OnboardingRequest, request: Request, user: CurrentUser, supabase_db: SupabaseDb
) -> UserProfileResponse:
    """Create the current user's profile from their onboarding answers and return it.

    Records that the user accepted the current privacy policy. Can only be done
    once per user.
    """
    row = {
        "id": str(user.user_id),
        "username": payload.username,
        "first_name": payload.first_name,
        "last_name": payload.last_name,
        "onboarding_answers": payload.answers,
        "privacy_policy_version": request.app.state.settings.privacy_policy_version,
        "privacy_policy_accepted_at": datetime.now(UTC).isoformat(),
    }
    try:
        created = await supabase_db.insert("users", row, PROFILE_COLUMNS)
    except SupabaseUnavailableError:
        database_unavailable()
    except SupabaseConflictError as exc:
        if exc.constraint == PROFILE_EXISTS_CONSTRAINT:
            _conflict("profile_exists", "Onboarding is already finished.")
        if exc.constraint == USERNAME_TAKEN_CONSTRAINT:
            _username_taken()
        raise
    return _to_profile(created)


def _to_profile(row: dict[str, Any]) -> UserProfileResponse:
    try:
        return UserProfileResponse(
            user_id=row["id"],
            username=row["username"],
            first_name=row["first_name"],
            last_name=row["last_name"],
            preferred_language=row["preferred_language"],
            verified=row["verified_at"] is not None,
            onboarding_answers=row["onboarding_answers"],
            created_at=row["created_at"],
        )
    except (KeyError, ValidationError):
        # A row missing columns or holding invalid values is a malformed response.
        database_unavailable()


def _profile_not_found() -> NoReturn:
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="No profile yet. Finish onboarding to create it.",
    )


def _username_taken() -> NoReturn:
    _conflict("username_taken", "That username is taken.")


def _conflict(code: Literal["profile_exists", "username_taken"], message: str) -> NoReturn:
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=ConflictDetail(code=code, message=message).model_dump(),
    )
