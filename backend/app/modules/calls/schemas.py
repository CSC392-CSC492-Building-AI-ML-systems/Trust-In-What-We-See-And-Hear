from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, StringConstraints

from app.api.schemas import RequestModel

CallStatus = Literal["queued", "in_progress", "completed", "failed", "canceled"]
CallOutcome = Literal["recognized_scam", "partially_complied", "fully_complied", "inconclusive"]
EndedBy = Literal["hangup", "time_limit", "completed", "error"]

STATUS_DESCRIPTION = (
    "`queued` until the browser connects to `ws_url`, then `in_progress`. "
    "Ends as `completed` (scored; see `summary`) or `failed` (not started in time, "
    "ended by an error, or could not be scored)."
)


class CallCreate(RequestModel):
    consent_id: UUID = Field(
        description="An accepted, unused, unexpired consent from `GET /consents`."
    )
    scenario_id: UUID = Field(description="A scenario from `GET /scenarios`.")


class CallResponse(BaseModel):
    id: UUID
    consent_id: UUID | None = Field(description="Null once the consent is deleted.")
    scenario_id: UUID | None = Field(description="Null once the scenario is deleted.")
    persona_name: str = Field(description="The friend whose voice was used, as at the call.")
    scenario_title: str = Field(description="The scenario's title, as at the call.")
    status: CallStatus = Field(description=STATUS_DESCRIPTION)
    outcome: CallOutcome | None = Field(description="Null until the call is `completed`.")
    score: int | None = Field(
        ge=0,
        le=100,
        description="0–100. Null until `completed`, and for an `inconclusive` call.",
    )
    created_at: datetime
    started_at: datetime | None
    ended_at: datetime | None
    duration_seconds: int | None


class CallStartResponse(CallResponse):
    ws_url: str = Field(
        description=(
            "Where the browser connects for the call's audio "
            "(voice-pipeline/PROTOCOL.md). Contains a one-time token that expires "
            "if it is not used within a couple of minutes. Not returned again."
        )
    )


class CallSummary(BaseModel):
    outcome: CallOutcome
    score: int | None = Field(description="Null for an `inconclusive` call.")
    summary: str
    did_well: list[str]
    to_improve: list[str]
    red_flags_noticed: list[str]
    red_flags_missed: list[str]
    sensitive_info_shared: bool
    duration_seconds: int | None


class CallDetailResponse(CallResponse):
    summary: CallSummary | None = Field(
        default=None, description="The call's result, once it is `completed`."
    )
    failure_reason: str | None = Field(
        default=None,
        description=(
            "Why a `failed` call failed: `timed_out` (not connected in time, or no "
            "result arrived), `error`, `not_scored` or `voice_unavailable`."
        ),
    )


# Internal: between the backend and the voice pipeline (voice-pipeline/BACKEND.md).

ClaimToken = Annotated[str, StringConstraints(min_length=1, max_length=256)]
Feedback = Annotated[list[Annotated[str, StringConstraints(max_length=1000)]], Field(max_length=20)]


class CallClaim(RequestModel):
    token: ClaimToken


class CallSetupResponse(BaseModel):
    context: dict = Field(description="The call context (app/modules/calls/context.py).")
    voice_url: str = Field(description="Signed https link to the persona's WAV sample.")
    max_duration_seconds: int


class CallResultIn(BaseModel):
    """Matches the `call_results` columns. Unknown fields are ignored."""

    outcome: CallOutcome
    score: int = Field(ge=0, le=100)
    summary: Annotated[str, StringConstraints(max_length=4000)]
    did_well: Feedback = Field(default_factory=list)
    to_improve: Feedback = Field(default_factory=list)
    red_flags_noticed: Feedback = Field(default_factory=list)
    red_flags_missed: Feedback = Field(default_factory=list)
    sensitive_info_shared: bool = False
    model_version: Annotated[str, StringConstraints(max_length=200)] | None = None


class CallReport(BaseModel):
    ended_by: EndedBy
    duration_seconds: int = Field(ge=0, le=24 * 60 * 60)
    result: CallResultIn | None = Field(
        description="Null when the call could not be scored, or never started."
    )
