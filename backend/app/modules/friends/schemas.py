from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.api.schemas import Pagination, RequestModel
from app.modules.users.public import PublicUser
from app.modules.users.schemas import Username


class FriendRequestCreate(RequestModel):
    username: Username = Field(description="Username of the user to send the request to.")


class FriendRequestQuery(Pagination):
    direction: Literal["incoming", "outgoing"] | None = Field(
        default=None,
        description="Only incoming or only outgoing requests. Both if omitted.",
    )


class FriendRequestResponse(BaseModel):
    id: UUID
    direction: Literal["incoming", "outgoing"] = Field(
        description="`incoming`: sent to the current user. `outgoing`: sent by them."
    )
    user: PublicUser = Field(description="The other user.")
    created_at: datetime


class FriendResponse(PublicUser):
    friends_since: datetime
