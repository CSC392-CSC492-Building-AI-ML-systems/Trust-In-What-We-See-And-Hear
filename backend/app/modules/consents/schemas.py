from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.api.schemas import Pagination, RequestModel
from app.modules.users.public import PublicUser


class ConsentRequestCreate(RequestModel):
    consenter_user_id: UUID = Field(
        description="The friend whose profile and voice the current user wants to use."
    )


class ConsentRequestQuery(Pagination):
    direction: Literal["incoming", "outgoing"] | None = Field(
        default=None,
        description=(
            "`incoming`: requests to use the current user's voice. "
            "`outgoing`: requests the current user sent. Both if omitted."
        ),
    )


class ConsentResponse(BaseModel):
    id: UUID
    direction: Literal["incoming", "outgoing"] = Field(
        description=(
            "`outgoing`: the current user asked to use the other user's voice. "
            "`incoming`: the other user asked to use the current user's voice."
        )
    )
    user: PublicUser = Field(description="The other user.")
    status: Literal["pending", "accepted", "declined", "revoked"] = Field(
        description="`revoked`: the consenter withdrew it after accepting, before it was used."
    )
    requested_at: datetime
    responded_at: datetime | None
    expires_at: datetime | None = Field(
        description="When an accepted consent stops being usable. Set on acceptance."
    )
    used_at: datetime | None = Field(
        description="When the consent was used to start a call. Each consent is used once."
    )
    usable: bool = Field(
        description=(
            "Whether the current user can start a call with it now: they are the "
            "requester, and it is accepted, unused, unexpired and the friend's voice "
            "is ready."
        )
    )
