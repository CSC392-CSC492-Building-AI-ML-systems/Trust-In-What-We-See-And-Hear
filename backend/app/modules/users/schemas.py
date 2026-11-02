from uuid import UUID

from pydantic import BaseModel, JsonValue

from app.api.schemas import RequestModel


class UpdateProfileRequest(RequestModel):
    fields: dict[str, JsonValue]


class UserProfileResponse(BaseModel):
    user_id: UUID
    onboarding_completed: bool
    fields: dict[str, JsonValue]


class OnboardingRequest(RequestModel):
    answers: dict[str, JsonValue]


class OnboardingResponse(BaseModel):
    onboarding_completed: bool
