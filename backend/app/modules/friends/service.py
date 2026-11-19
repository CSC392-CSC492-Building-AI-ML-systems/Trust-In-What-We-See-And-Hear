"""Friendship queries shared with other modules (consents and calls).

AI-assisted code (written with Claude Code), cited per the CSC398 AI policy.
"""

from typing import Any
from uuid import UUID

from app.integrations.supabase import SupabaseDatabase
from app.modules.users.public import PUBLIC_USER_COLUMNS


def pair_filter(first: UUID, second: UUID, first_column: str, second_column: str) -> str:
    """PostgREST `or` filter matching a row between two users, in either direction."""
    return (
        f"(and({first_column}.eq.{first},{second_column}.eq.{second}),"
        f"and({first_column}.eq.{second},{second_column}.eq.{first}))"
    )


FRIENDSHIP_COLUMNS = (
    # Both notes are read; only the current user's own is ever returned (own_notes).
    "id,status,created_at,accepted_at,requester_id,addressee_id,requester_notes,addressee_notes,"
    f"requester:users!friendships_requester_id_fkey({PUBLIC_USER_COLUMNS}),"
    f"addressee:users!friendships_addressee_id_fkey({PUBLIC_USER_COLUMNS})"
)


def notes_column(row: dict[str, Any], user_id: UUID) -> str:
    """The column holding `user_id`'s own note about the other user in this friendship.

    The row is shared by both friends, so each has a column of their own.
    """
    return "requester_notes" if row["requester_id"] == str(user_id) else "addressee_notes"


def own_notes(row: dict[str, Any], user_id: UUID) -> str | None:
    """`user_id`'s note about the other user. Never the other user's note about them."""
    return row.get(notes_column(row, user_id))


async def find_friendship(
    db: SupabaseDatabase, first: UUID, second: UUID
) -> dict[str, Any] | None:
    """Return the friendship row between two users (pending or accepted), if any.

    Columns: id, status, requester_id, addressee_id.
    """
    rows = await db.select(
        "friendships",
        {"or": pair_filter(first, second, "requester_id", "addressee_id")},
        "id,status,requester_id,addressee_id",
    )
    return rows[0] if rows else None
