from fastapi import APIRouter

from app.core.errors import not_implemented
from app.modules.users.schemas import (
    OnboardingRequest,
    OnboardingResponse,
    UpdateProfileRequest,
    UserProfileResponse,
)

router = APIRouter(prefix="/users/me", tags=["Users"])


@router.get("", response_model=UserProfileResponse)
async def get_user_info() -> UserProfileResponse:
    """Return the current user's profile."""
    not_implemented("Profile retrieval")


@router.put("", response_model=UserProfileResponse)
async def edit_user_info(payload: UpdateProfileRequest) -> UserProfileResponse:
    """Update the current user's profile, including removing stored onboarding answers.

    **TBD:** whether omitted fields are kept or cleared, and how answers are removed.
    """
    not_implemented("Profile editing")


@router.post("/onboarding", response_model=OnboardingResponse)
async def finish_onboarding(payload: OnboardingRequest) -> OnboardingResponse:
    """Save onboarding answers to the current user's profile and mark onboarding complete."""
    not_implemented("Onboarding")
