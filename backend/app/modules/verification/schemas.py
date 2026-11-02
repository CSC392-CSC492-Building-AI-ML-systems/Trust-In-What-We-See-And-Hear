from uuid import UUID

from pydantic import BaseModel


class VerificationResponse(BaseModel):
    verification_id: UUID
    status: str
