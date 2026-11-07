from uuid import UUID

from pydantic import BaseModel, Field, JsonValue

from app.api.schemas import RequestModel

PROFILE_FIELDS_DESCRIPTION = "Profile fields. The field set is TBD."


class UpdateProfileRequest(RequestModel):
    fields: dict[str, JsonValue] = Field(description=PROFILE_FIELDS_DESCRIPTION)


class UserProfileResponse(BaseModel):
    user_id: UUID
    fields: dict[str, JsonValue] = Field(description=PROFILE_FIELDS_DESCRIPTION)


class OnboardingRequest(RequestModel):
    answers: dict[str, JsonValue] = Field(
        description="Answers to the onboarding questions. The question set is TBD."
    )
