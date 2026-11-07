from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, Field, JsonValue, StringConstraints, model_validator

from app.api.schemas import RequestModel

Username = Annotated[
    str,
    Field(
        pattern=r"^[a-z0-9_]{3,30}$",
        description=(
            "3–30 lowercase letters, numbers or underscores. Unique; how friends find you."
        ),
        examples=["alex_morgan"],
    ),
]
DisplayName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=50),
    Field(description="Name shown to friends.", examples=["Alex Morgan"]),
]
PreferredLanguage = Annotated[
    str,
    Field(
        pattern=r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*$",
        description="Language tag, e.g. `en` or `fr-CA`.",
        examples=["en"],
    ),
]
OnboardingAnswers = Annotated[
    dict[str, JsonValue],
    Field(
        description=(
            "Answers to the onboarding questions, stored as sent. "
            "The question set is TBD."
        ),
        examples=[{"age_range": "25-34", "interests": ["technology", "travel"]}],
    ),
]


class UserProfileResponse(BaseModel):
    user_id: UUID
    username: str
    display_name: str
    preferred_language: str
    verified: bool = Field(description="Whether identity verification has passed.")
    onboarding_answers: dict[str, JsonValue] | None = Field(
        description="Null if never given or removed by the user."
    )
    created_at: datetime = Field(description="When the profile was created (onboarding finished).")


class OnboardingRequest(RequestModel):
    username: Username
    display_name: DisplayName
    accept_privacy_policy: Literal[True] = Field(
        description="Must be `true`: the user accepts the current privacy policy."
    )
    answers: OnboardingAnswers


class UpdateProfileRequest(RequestModel):
    """Only the fields that are sent are changed; omitted fields are kept."""

    username: Username | None = None
    display_name: DisplayName | None = None
    preferred_language: PreferredLanguage | None = None
    onboarding_answers: OnboardingAnswers | None = Field(
        default=None, description="Send `null` to remove the stored answers."
    )

    @model_validator(mode="after")
    def only_answers_can_be_null(self) -> Self:
        for name in ("username", "display_name", "preferred_language"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null.")
        return self


class ConflictDetail(BaseModel):
    code: Literal["profile_exists", "username_taken"]
    message: str


class ConflictResponse(BaseModel):
    detail: ConflictDetail
