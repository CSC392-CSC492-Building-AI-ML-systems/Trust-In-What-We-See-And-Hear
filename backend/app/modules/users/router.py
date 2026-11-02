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
    not_implemented("Profile retrieval")


@router.put("", response_model=UserProfileResponse)
async def edit_user_info(payload: UpdateProfileRequest) -> UserProfileResponse:
    not_implemented("Profile editing")


@router.post("/onboarding", response_model=OnboardingResponse)
async def finish_onboarding(payload: OnboardingRequest) -> OnboardingResponse:
    not_implemented("Onboarding")
