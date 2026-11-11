from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, Field, StringConstraints, model_validator

from app.api.schemas import RequestModel

ScamCategory = Literal[
    "grandparent_emergency",
    "executive_impersonation",
    "bank_fraud_alert",
    "government_agency",
    "tech_support",
    "romance",
    "prize_lottery",
    "other",
]
Difficulty = Literal["easy", "medium", "hard"]

Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Description = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=1000),
    Field(description="Shown to the user when choosing a scenario."),
]
SystemPrompt = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=8000),
    Field(
        description=(
            "Instructions for the scammer agent. **Provisional:** the format the "
            "self-hosted call agent needs is not decided yet."
        )
    ),
]
RedFlag = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
RedFlags = Annotated[
    list[RedFlag],
    Field(
        max_length=20,
        description="Warning signs the user should notice, e.g. `urgency`, `gift cards`.",
    ),
]
MaxDuration = Annotated[
    int, Field(ge=60, le=1800, description="Longest the call may run, in seconds.")
]


class ScenarioCreate(RequestModel):
    title: Title
    category: ScamCategory
    difficulty: Difficulty = "medium"
    description: Description
    system_prompt: SystemPrompt
    red_flags: RedFlags = Field(default_factory=list)
    max_duration_seconds: MaxDuration = 300


class ScenarioUpdate(RequestModel):
    """Only the fields that are sent are changed; omitted fields are kept."""

    title: Title | None = None
    category: ScamCategory | None = None
    difficulty: Difficulty | None = None
    description: Description | None = None
    system_prompt: SystemPrompt | None = None
    red_flags: RedFlags | None = None
    max_duration_seconds: MaxDuration | None = None

    @model_validator(mode="after")
    def no_nulls(self) -> Self:
        for name in self.model_fields_set:
            if getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null.")
        return self


class ScenarioResponse(BaseModel):
    id: UUID
    built_in: bool = Field(
        description="Built-in scenarios are managed by the team and cannot be edited."
    )
    title: str
    category: ScamCategory
    difficulty: Difficulty
    description: str
    system_prompt: str | None = Field(
        description="Only returned for the current user's own scenarios; null for built-in ones."
    )
    red_flags: list[str]
    max_duration_seconds: int
    created_at: datetime
    updated_at: datetime
