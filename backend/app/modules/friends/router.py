from typing import Annotated, Any, Literal, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, status

from app.api.schemas import Page, Pagination
from app.core import clock
from app.core.auth import AuthenticatedUser, get_current_user
from app.core.errors import api_error, error_responses, merge_responses
from app.integrations.supabase import (
    SupabaseConflictError,
    SupabaseDatabase,
    get_supabase_db,
)
from app.modules.friends.schemas import (
    FriendRequestCreate,
    FriendRequestQuery,
    FriendRequestResponse,
    FriendResponse,
)
from app.modules.friends.service import FRIENDSHIP_COLUMNS, pair_filter
from app.modules.users.public import PUBLIC_USER_COLUMNS, to_public_user

router = APIRouter(tags=["Friends"])

CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_user)]
SupabaseDb = Annotated[SupabaseDatabase, Depends(get_supabase_db)]

FRIENDSHIP_PAIR_CONSTRAINT = "friendships_pair_idx"

REQUEST_NOT_FOUND = error_responses(
    status.HTTP_404_NOT_FOUND,
    ("friend_request_not_found", "No pending request with this ID was sent to you."),
)


@router.post(
    "/friend-requests",
    response_model=FriendRequestResponse,
    status_code=status.HTTP_201_CREATED,
    responses=merge_responses(
        error_responses(
            status.HTTP_400_BAD_REQUEST,
            ("cannot_friend_self", "The username is the current user's own."),
        ),
        error_responses(
            status.HTTP_404_NOT_FOUND, ("user_not_found", "No user has this username.")
        ),
        error_responses(
            status.HTTP_409_CONFLICT,
            ("profile_required", "The current user has not finished onboarding."),
            ("already_friends", "The two users are already friends."),
            ("friend_request_exists", "The current user already sent this user a request."),
            (
                "incoming_friend_request",
                "This user already sent the current user a request; accept it instead.",
            ),
        ),
    ),
)
async def send_friend_request(
    payload: FriendRequestCreate, user: CurrentUser, supabase_db: SupabaseDb
) -> FriendRequestResponse:
    """Send a friend request to the user with the given username."""
    rows = await supabase_db.select(
        "users",
        {"or": f"(id.eq.{user.user_id},username.eq.{payload.username})"},
        PUBLIC_USER_COLUMNS,
    )
    me = next((row for row in rows if row["id"] == str(user.user_id)), None)
    if me is None:
        api_error(status.HTTP_409_CONFLICT, "profile_required", "Finish onboarding first.")
    if me["username"] == payload.username:
        api_error(
            status.HTTP_400_BAD_REQUEST,
            "cannot_friend_self",
            "You cannot send a friend request to yourself.",
        )
    addressee = next((row for row in rows if row["username"] == payload.username), None)
    if addressee is None:
        api_error(status.HTTP_404_NOT_FOUND, "user_not_found", "No user has that username.")

    existing = await supabase_db.select(
        "friendships",
        {"or": pair_filter(user.user_id, addressee["id"], "requester_id", "addressee_id")},
        "status,requester_id",
    )
    if existing:
        _existing_friendship_conflict(existing[0], user.user_id)

    try:
        created = await supabase_db.insert(
            "friendships",
            {"requester_id": str(user.user_id), "addressee_id": addressee["id"]},
            FRIENDSHIP_COLUMNS,
        )
    except SupabaseConflictError as exc:
        # Another request between the two users was created at the same time.
        if exc.constraint == FRIENDSHIP_PAIR_CONSTRAINT:
            api_error(
                status.HTTP_409_CONFLICT,
                "friend_request_exists",
                "A friend request between you already exists.",
            )
        raise
    return _to_request(created, user.user_id)


@router.get("/friend-requests", response_model=Page[FriendRequestResponse])
async def get_friend_requests(
    pagination: Annotated[FriendRequestQuery, Query()],
    user: CurrentUser,
    supabase_db: SupabaseDb,
) -> Page[FriendRequestResponse]:
    """List the current user's pending friend requests, newest first."""
    direction = pagination.direction
    if direction == "incoming":
        filters = {"addressee_id": f"eq.{user.user_id}"}
    elif direction == "outgoing":
        filters = {"requester_id": f"eq.{user.user_id}"}
    else:
        filters = {"or": f"(requester_id.eq.{user.user_id},addressee_id.eq.{user.user_id})"}
    rows = await supabase_db.select(
        "friendships",
        {**filters, "status": "eq.pending"},
        FRIENDSHIP_COLUMNS,
        order="created_at.desc,id",
        limit=pagination.limit,
        offset=pagination.offset,
    )
    return Page(
        items=[_to_request(row, user.user_id) for row in rows],
        limit=pagination.limit,
        offset=pagination.offset,
    )


@router.post(
    "/friend-requests/{id}/accept",
    response_model=FriendResponse,
    responses=REQUEST_NOT_FOUND,
)
async def accept_friend_request(
    id: UUID, user: CurrentUser, supabase_db: SupabaseDb
) -> FriendResponse:
    """Accept a pending request sent to the current user and return the new friend."""
    rows = await supabase_db.update(
        "friendships",
        {"id": f"eq.{id}", "addressee_id": f"eq.{user.user_id}", "status": "eq.pending"},
        {"status": "accepted", "accepted_at": clock.to_iso(clock.now())},
        FRIENDSHIP_COLUMNS,
    )
    if not rows:
        _request_not_found()
    return _to_friend(rows[0], user.user_id)


@router.post(
    "/friend-requests/{id}/decline",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=REQUEST_NOT_FOUND,
)
async def decline_friend_request(id: UUID, user: CurrentUser, supabase_db: SupabaseDb) -> None:
    """Decline a pending request sent to the current user. The request is deleted."""
    rows = await supabase_db.delete(
        "friendships",
        {"id": f"eq.{id}", "addressee_id": f"eq.{user.user_id}", "status": "eq.pending"},
        "id",
    )
    if not rows:
        _request_not_found()


@router.get("/friends", response_model=Page[FriendResponse])
async def get_friends_list(
    pagination: Annotated[Pagination, Query()], user: CurrentUser, supabase_db: SupabaseDb
) -> Page[FriendResponse]:
    """List the current user's friends, most recent first."""
    rows = await supabase_db.select(
        "friendships",
        {
            "or": f"(requester_id.eq.{user.user_id},addressee_id.eq.{user.user_id})",
            "status": "eq.accepted",
        },
        FRIENDSHIP_COLUMNS,
        order="accepted_at.desc,id",
        limit=pagination.limit,
        offset=pagination.offset,
    )
    return Page(
        items=[_to_friend(row, user.user_id) for row in rows],
        limit=pagination.limit,
        offset=pagination.offset,
    )


@router.delete(
    "/friends/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=error_responses(
        status.HTTP_404_NOT_FOUND, ("friend_not_found", "This user is not a friend.")
    ),
)
async def remove_friend(
    user_id: Annotated[UUID, Path(description="The friend's user ID.")],
    user: CurrentUser,
    supabase_db: SupabaseDb,
) -> None:
    """Remove a friend, for both users.

    The consents between the two users belong to the friendship, so the
    database deletes them with it, in the same statement. Becoming friends
    again never brings them back.
    """
    rows = await supabase_db.delete(
        "friendships",
        {
            "or": pair_filter(user.user_id, user_id, "requester_id", "addressee_id"),
            "status": "eq.accepted",
        },
        "id",
    )
    if not rows:
        api_error(status.HTTP_404_NOT_FOUND, "friend_not_found", "That user is not your friend.")


def _existing_friendship_conflict(row: dict[str, Any], user_id: UUID) -> NoReturn:
    if row["status"] == "accepted":
        api_error(status.HTTP_409_CONFLICT, "already_friends", "You are already friends.")
    if row["requester_id"] == str(user_id):
        api_error(
            status.HTTP_409_CONFLICT,
            "friend_request_exists",
            "You already sent this user a friend request.",
        )
    api_error(
        status.HTTP_409_CONFLICT,
        "incoming_friend_request",
        "This user already sent you a friend request. Accept it instead.",
    )


def _other_user(row: dict[str, Any], user_id: UUID) -> tuple[Literal["incoming", "outgoing"], dict]:
    if row["requester_id"] == str(user_id):
        return "outgoing", row["addressee"]
    return "incoming", row["requester"]


def _to_request(row: dict[str, Any], user_id: UUID) -> FriendRequestResponse:
    direction, other = _other_user(row, user_id)
    return FriendRequestResponse(
        id=row["id"],
        direction=direction,
        user=to_public_user(other),
        created_at=row["created_at"],
    )


def _to_friend(row: dict[str, Any], user_id: UUID) -> FriendResponse:
    _, other = _other_user(row, user_id)
    return FriendResponse(
        **to_public_user(other).model_dump(), friends_since=row["accepted_at"]
    )


def _request_not_found() -> NoReturn:
    api_error(
        status.HTTP_404_NOT_FOUND,
        "friend_request_not_found",
        "No pending friend request with that ID was sent to you.",
    )
