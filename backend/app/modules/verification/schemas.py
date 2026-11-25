from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

VerificationStatus = Literal[
    "not_started",
    "in_progress",
    "in_review",
    "awaiting_user",
    "resubmitted",
    "approved",
    "declined",
    "expired",
    "abandoned",
    "kyc_expired",
]

STATUS_DESCRIPTION = (
    "The provider's (Didit's) status for the session. `not_started` and "
    "`in_progress` until the user finishes; then `approved`, `declined`, or "
    "`in_review` while someone at Didit checks it by hand. `awaiting_user` and "
    "`resubmitted` mean Didit asked the user to redo a step. `expired` and "
    "`abandoned` sessions were not finished. `kyc_expired` means an approved "
    "document has since expired."
)


class VerificationStartResponse(BaseModel):
    verification_id: UUID
    status: VerificationStatus = Field(description=STATUS_DESCRIPTION)
    url: str = Field(
        description=(
            "Didit's page where the user verifies. Open it (redirect, new tab or "
            "iframe); when the user finishes, Didit sends them to the return URL "
            "the server is set up with."
        )
    )


class VerificationSummary(BaseModel):
    id: UUID
    status: VerificationStatus = Field(description=STATUS_DESCRIPTION)
    created_at: datetime
    updated_at: datetime


class VerificationStateResponse(BaseModel):
    verified: bool = Field(description="Whether the user's identity is verified.")
    verification: VerificationSummary | None = Field(
        description="The user's latest verification session; null if they never started one."
    )
