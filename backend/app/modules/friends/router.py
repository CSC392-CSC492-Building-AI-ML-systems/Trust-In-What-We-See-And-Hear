from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from app.api.schemas import Page, Pagination
from app.core.errors import not_implemented
from app.modules.friends.schemas import (
    FriendRequestCreate,
    FriendRequestResponse,
    FriendResponse,
)

router = APIRouter(tags=["Friends"])


@router.post("/friend-requests", response_model=FriendRequestResponse, status_code=201)
async def send_friend_request(payload: FriendRequestCreate) -> FriendRequestResponse:
    not_implemented("Sending friend requests")


@router.get("/friend-requests", response_model=Page[FriendRequestResponse])
async def get_friend_requests(
    pagination: Annotated[Pagination, Query()],
) -> Page[FriendRequestResponse]:
    """List the current user's pending incoming and outgoing requests."""
    not_implemented("Friend request listing")


@router.post("/friend-requests/{id}/accept", response_model=FriendRequestResponse)
async def accept_friend_request(id: UUID) -> FriendRequestResponse:
    """Acceptance creates a mutual friendship."""
    not_implemented("Accepting friend requests")


@router.post("/friend-requests/{id}/decline", response_model=FriendRequestResponse)
async def decline_friend_request(id: UUID) -> FriendRequestResponse:
    not_implemented("Declining friend requests")


@router.get("/friends", response_model=Page[FriendResponse])
async def get_friends_list(
    pagination: Annotated[Pagination, Query()],
) -> Page[FriendResponse]:
    not_implemented("Friends listing")


@router.delete("/friends/{user_id}", status_code=204)
async def remove_friend(user_id: UUID) -> None:
    not_implemented("Friend removal")
