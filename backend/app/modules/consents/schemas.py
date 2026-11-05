from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, Field

from app.api.schemas import RequestModel


class ConsentRequestCreate(RequestModel):
    recipient_user_id: UUID = Field(
        description="Friend whose profile and voice the requester wants to use."
    )


class ConsentResponse(BaseModel):
    id: UUID
    requester_user_id: UUID
    recipient_user_id: UUID
    status: Literal["pending", "accepted", "declined"]
    expires_at: AwareDatetime | None = Field(
        default=None,
        description="When the consent stops being usable. Set by the server on acceptance.",
    )
