import json
from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    Field,
    JsonValue,
    StringConstraints,
    model_validator,
)
from pydantic.json_schema import SkipJsonSchema

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
FirstName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=50),
    Field(description="First name, shown to friends.", examples=["Alex"]),
]
LastName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=50),
    Field(description="Last name, shown to friends.", examples=["Morgan"]),
]
PreferredLanguage = Annotated[
    str,
    Field(
        pattern=r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*$",
        # RFC 5646 asks implementations to support tags of at least 35 characters.
        max_length=35,
        description="Language tag, e.g. `en` or `fr-CA`.",
        examples=["en"],
    ),
]
# Until the question set is defined, cap the answers by their size as stored.
MAX_ANSWERS_BYTES = 10_000


def _check_answers(answers: dict[str, JsonValue]) -> dict[str, JsonValue]:
    try:
        # NaN and Infinity are not valid JSON, so the database request could not send them.
        encoded = json.dumps(answers, allow_nan=False, separators=(",", ":"))
    except ValueError:
        raise ValueError("Answers cannot contain NaN or Infinity.") from None
    if len(encoded.encode()) > MAX_ANSWERS_BYTES:
        raise ValueError(f"Answers cannot be larger than {MAX_ANSWERS_BYTES} bytes.")
    return answers


OnboardingAnswers = Annotated[
    dict[str, JsonValue],
    AfterValidator(_check_answers),
    Field(
        description=(
            "Answers to the onboarding questions, stored as sent. "
            "The question set is TBD. At most 10,000 bytes as JSON."
        ),
        examples=[{"age_range": "25-34", "interests": ["technology", "travel"]}],
    ),
]


class UserProfileResponse(BaseModel):
    user_id: UUID
    username: str
    first_name: str
    last_name: str
    preferred_language: str
    verified: bool = Field(description="Whether identity verification has passed.")
    onboarding_answers: dict[str, JsonValue] | None = Field(
        description="Null if never given or removed by the user."
    )
    created_at: datetime = Field(description="When the profile was created (onboarding finished).")


class OnboardingRequest(RequestModel):
    username: Username
    first_name: FirstName
    last_name: LastName
    accept_privacy_policy: Literal[True] = Field(
        description="Must be `true`: the user accepts the current privacy policy."
    )
    answers: OnboardingAnswers


class UpdateProfileRequest(RequestModel):
    """Only the fields that are sent are changed; omitted fields are kept."""

    # `SkipJsonSchema[None]` keeps None as the default for omitted fields without
    # documenting `null` as allowed; the validator below rejects an explicit null.
    username: Username | SkipJsonSchema[None] = None
    first_name: FirstName | SkipJsonSchema[None] = None
    last_name: LastName | SkipJsonSchema[None] = None
    preferred_language: PreferredLanguage | SkipJsonSchema[None] = None
    onboarding_answers: OnboardingAnswers | None = Field(
        default=None, description="Send `null` to remove the stored answers."
    )

    @model_validator(mode="after")
    def only_answers_can_be_null(self) -> Self:
        for name in ("username", "first_name", "last_name", "preferred_language"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null.")
        return self


class ConflictDetail(BaseModel):
    code: Literal["profile_exists", "username_taken"]
    message: str


class ConflictResponse(BaseModel):
    detail: ConflictDetail
