"""Verification rules for the user routes.

A session's life:

1. `POST /users/me/verification` creates a Didit session for the user and
   records it (`identity_verifications`). The user verifies on Didit's page.
2. `GET /users/me/verification` asks Didit for the status while the session
   is unsettled and records it. An `approved` session sets `users.verified_at`.

There is no webhook: the result is picked up when the user (or the frontend,
polling after they return from Didit) reads their verification.

AI-assisted code (written with Claude Code), cited per the CSC398 AI policy.
"""

import logging
from typing import Any
from uuid import UUID

from app.integrations.identity_verification import DiditClient, VerificationSession
from app.integrations.supabase import SupabaseConflictError, SupabaseDatabase
from app.modules.consents.service import DATABASE_NOW

logger = logging.getLogger(__name__)

TABLE = "identity_verifications"
COLUMNS = "id,user_id,provider_session_id,status,created_at,updated_at"

# Statuses that are not checked with Didit again. Only someone at Didit acting
# by hand, or the document expiring, could change them.
SETTLED = frozenset({"approved", "declined", "expired", "kyc_expired"})


async def latest_verification(db: SupabaseDatabase, user_id: UUID) -> dict[str, Any] | None:
    rows = await db.select(
        TABLE, {"user_id": f"eq.{user_id}"}, COLUMNS, order="created_at.desc", limit=1
    )
    return rows[0] if rows else None


async def record_session(
    db: SupabaseDatabase, user_id: UUID, session: VerificationSession
) -> dict[str, Any]:
    """Store a new session, or update the stored one Didit handed back again."""
    try:
        return await db.insert(
            TABLE,
            {
                "user_id": str(user_id),
                "provider_session_id": session.session_id,
                "status": session.status,
            },
            COLUMNS,
        )
    except SupabaseConflictError:
        # Didit returns the user's unfinished session instead of a new one.
        rows = await db.select(
            TABLE,
            {"provider_session_id": f"eq.{session.session_id}", "user_id": f"eq.{user_id}"},
            COLUMNS,
        )
        if not rows:
            raise
        row, _ = await apply_status(db, rows[0], session.status)
        return row


async def sync(
    db: SupabaseDatabase, didit: DiditClient, row: dict[str, Any]
) -> tuple[dict[str, Any], bool]:
    """Read a session's status from Didit and apply it. See `apply_status`.

    Raises IdentityVerificationError if Didit cannot answer.
    """
    new_status, vendor_data = await didit.session_status(row["provider_session_id"])
    if vendor_data is not None and vendor_data != str(row["user_id"]):
        # Sessions are created with vendor_data set to the user's ID.
        logger.error("Didit session %s belongs to another user; ignored.", row["id"])
        return row, False
    return await apply_status(db, row, new_status)


async def apply_status(
    db: SupabaseDatabase, row: dict[str, Any], new_status: str
) -> tuple[dict[str, Any], bool]:
    """Record a session's new status, and verify its user if it is approved.

    Returns the row and whether the user was verified by this change.
    """
    if new_status == row["status"]:
        return row, False
    updated = await db.update(TABLE, {"id": f"eq.{row['id']}"}, {"status": new_status}, COLUMNS)
    row = updated[0] if updated else {**row, "status": new_status}
    if new_status != "approved":
        return row, False
    await db.update(
        "users",
        # An earlier verification's time is kept.
        {"id": f"eq.{row['user_id']}", "verified_at": "is.null"},
        {"verified_at": DATABASE_NOW},
        "id",
    )
    return row, True
