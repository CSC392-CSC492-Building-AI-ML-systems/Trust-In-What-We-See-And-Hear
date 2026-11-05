from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Path, Query

from app.api.schemas import Page, Pagination
from app.core.errors import NOT_FOUND_RESPONSES, not_implemented
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


@router.post(
    "/friend-requests/{id}/accept",
    response_model=FriendRequestResponse,
    responses=NOT_FOUND_RESPONSES,
)
async def accept_friend_request(id: UUID) -> FriendRequestResponse:
    """Accept a request sent to the current user. Both users become friends."""
    not_implemented("Accepting friend requests")


@router.post(
    "/friend-requests/{id}/decline",
    response_model=FriendRequestResponse,
    responses=NOT_FOUND_RESPONSES,
)
async def decline_friend_request(id: UUID) -> FriendRequestResponse:
    """Decline a request sent to the current user."""
    not_implemented("Declining friend requests")


@router.get("/friends", response_model=Page[FriendResponse])
async def get_friends_list(
    pagination: Annotated[Pagination, Query()],
) -> Page[FriendResponse]:
    not_implemented("Friends listing")


@router.delete("/friends/{user_id}", status_code=204, responses=NOT_FOUND_RESPONSES)
async def remove_friend(
    user_id: Annotated[UUID, Path(description="The friend's user ID.")],
) -> None:
    """Remove a friend.

    **TBD:** whether this also removes the current user from the other
    user's friends list.
    """
    not_implemented("Friend removal")
