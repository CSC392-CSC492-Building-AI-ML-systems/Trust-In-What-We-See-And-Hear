from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from app.api.schemas import RequestModel


class FriendRequestCreate(RequestModel):
    recipient_user_id: UUID


class FriendRequestResponse(BaseModel):
    id: UUID
    requester_user_id: UUID
    recipient_user_id: UUID
    status: Literal["pending", "accepted", "declined"]


class FriendResponse(BaseModel):
    user_id: UUID
