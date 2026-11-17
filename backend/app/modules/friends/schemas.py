from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, StringConstraints

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


NOTES_MAX_LENGTH = 100

Notes = Annotated[
    str,
    StringConstraints(strip_whitespace=True, max_length=NOTES_MAX_LENGTH),
    Field(
        description=(
            "The current user's private note about this friend, e.g. `We met at UofT. "
            "Calls me Bo.` The friend never sees it. When the current user starts a "
            "call with this friend's voice, the AI caller is given it."
        )
    ),
]


class FriendNotesUpdate(RequestModel):
    notes: Notes | None = Field(description="The new note. Null or an empty string clears it.")


class FriendResponse(PublicUser):
    friends_since: datetime
    notes: str | None = Field(
        description="The current user's private note about this friend; null if none."
    )
