"""Consent rules shared with other modules.

A consent lets the requester run one simulated call that uses the consenter's
profile and voice. It is usable while all of these hold:

- the consenter accepted it,
- it has not been used (each consent starts at most one call),
- it has not expired (`expires_at` is set on acceptance; an unused consent
  must not stay usable for so long that the consenter forgets giving it),
- the two users are still friends,
- the consenter's voice is ready.
"""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from app.core import clock
from app.integrations.supabase import SupabaseDatabase
from app.modules.consents.schemas import ConsentResponse
from app.modules.friends.service import are_friends
from app.modules.users.public import PUBLIC_USER_COLUMNS, to_public_user

CONSENT_COLUMNS = (
    "id,status,requested_at,responded_at,expires_at,used_at,requester_id,consenter_id,"
    f"requester:users!scam_consents_requester_id_fkey({PUBLIC_USER_COLUMNS}),"
    f"consenter:users!scam_consents_consenter_id_fkey({PUBLIC_USER_COLUMNS})"
)

# Voice statuses in which the voice can be used in a call. Revisit once the
# voice pipeline decides what "ready" means for zero-shot cloning.
USABLE_VOICE_STATUSES = frozenset({"ready"})

type ConsentProblem = Literal["consent_unusable", "not_friends", "voice_not_ready"]


class ConsentNotUsableError(Exception):
    def __init__(self, code: ConsentProblem, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def usable_filters(requester_id: UUID, now: datetime) -> dict[str, str]:
    """PostgREST filters for the requester's accepted, unused, unexpired consents.

    Friendship and voice are checked separately.
    """
    return {
        "requester_id": f"eq.{requester_id}",
        "status": "eq.accepted",
        "used_at": "is.null",
        "expires_at": f"gt.{clock.to_iso(now)}",
    }


def is_usable(row: dict[str, Any], now: datetime) -> bool:
    return (
        row["status"] == "accepted"
        and row["used_at"] is None
        and row["expires_at"] is not None
        and datetime.fromisoformat(row["expires_at"]) > now
    )


async def voice_is_ready(db: SupabaseDatabase, user_id: UUID) -> bool:
    rows = await db.select("voices", {"user_id": f"eq.{user_id}"}, "status")
    return bool(rows) and rows[0]["status"] in USABLE_VOICE_STATUSES


async def use_consent(
    db: SupabaseDatabase, consent_id: UUID, requester_id: UUID
) -> dict[str, Any]:
    """Check that a consent can start a call, then mark it used.

    For the calls module: call this when a call starts. Marking the consent
    used is a single conditional update, so two calls started at the same time
    cannot both use it. Raises ConsentNotUsableError if it cannot be used.
    Returns the consent row (CONSENT_COLUMNS) after marking it used.
    """
    now = clock.now()
    rows = await db.select(
        "scam_consents",
        {"id": f"eq.{consent_id}", **usable_filters(requester_id, now)},
        "id,consenter_id",
    )
    if not rows:
        raise ConsentNotUsableError(
            "consent_unusable",
            "This consent does not exist, is not accepted, was already used or expired.",
        )
    consenter_id = UUID(rows[0]["consenter_id"])
    if not await are_friends(db, requester_id, consenter_id):
        raise ConsentNotUsableError("not_friends", "You are no longer friends with this user.")
    if not await voice_is_ready(db, consenter_id):
        raise ConsentNotUsableError("voice_not_ready", "This user's voice is not ready.")

    used = await db.update(
        "scam_consents",
        {"id": f"eq.{consent_id}", **usable_filters(requester_id, now)},
        {"used_at": clock.to_iso(now)},
        CONSENT_COLUMNS,
    )
    if not used:
        # Used or expired since the check above.
        raise ConsentNotUsableError(
            "consent_unusable", "This consent was already used or has expired."
        )
    return used[0]


def to_consent(row: dict[str, Any], user_id: UUID, now: datetime) -> ConsentResponse:
    if row["requester_id"] == str(user_id):
        direction, other = "outgoing", row["consenter"]
    else:
        direction, other = "incoming", row["requester"]
    return ConsentResponse(
        id=row["id"],
        direction=direction,
        user=to_public_user(other),
        status=row["status"],
        requested_at=row["requested_at"],
        responded_at=row["responded_at"],
        expires_at=row["expires_at"],
        used_at=row["used_at"],
        usable=is_usable(row, now),
    )
