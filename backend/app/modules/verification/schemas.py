from uuid import UUID

from pydantic import BaseModel, Field


class VerificationResponse(BaseModel):
    verification_id: UUID
    status: str = Field(description="Verification status. Values TBD.")
