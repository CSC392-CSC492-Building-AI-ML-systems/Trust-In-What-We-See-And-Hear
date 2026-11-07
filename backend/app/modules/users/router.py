from fastapi import APIRouter

from app.core.errors import NOT_FOUND_RESPONSES, not_implemented
from app.modules.users.schemas import (
    OnboardingRequest,
    UpdateProfileRequest,
    UserProfileResponse,
)

router = APIRouter(prefix="/users/me", tags=["Users"])


@router.get("", response_model=UserProfileResponse, responses=NOT_FOUND_RESPONSES)
async def get_user_info() -> UserProfileResponse:
    """Return the current user's profile.

    Returns `404` if the user has not finished onboarding, because the profile
    is only created then.
    """
    not_implemented("Profile retrieval")


@router.put("", response_model=UserProfileResponse)
async def edit_user_info(payload: UpdateProfileRequest) -> UserProfileResponse:
    """Update the current user's profile, including removing stored onboarding answers.

    **TBD:** whether omitted fields are kept or cleared, and how answers are removed.
    """
    not_implemented("Profile editing")


@router.post("/onboarding", response_model=UserProfileResponse, status_code=201)
async def finish_onboarding(payload: OnboardingRequest) -> UserProfileResponse:
    """Create the current user's profile from their onboarding answers and return it.

    **TBD:** the request will also need `username`, `display_name` and privacy
    policy acceptance, which the profile requires.
    """
    not_implemented("Onboarding")
