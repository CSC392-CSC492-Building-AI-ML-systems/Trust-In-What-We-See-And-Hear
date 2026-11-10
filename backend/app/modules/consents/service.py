"""Consent rules shared with other modules.

A consent lets the requester run one simulated call that uses the consenter's
profile and voice. It is usable while all of these hold:

- the consenter accepted it, and has not revoked it,
- it has not been used (each consent starts at most one call),
- it has not expired (`expires_at` is set on acceptance; an unused consent
  must not stay usable for so long that the consenter forgets giving it),
- the consenter's voice is ready.

Each consent belongs to the friendship it was requested under
(`friendship_id`, deleted with it), so it exists only while the two users are
friends, and a later friendship never revives it.

Time conditions in queries use the database's clock (`now`), so a request
that arrives late cannot use a consent that expired in the meantime.
"""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from app.integrations.supabase import SupabaseDatabase
from app.modules.consents.schemas import ConsentResponse
from app.modules.users.public import PUBLIC_USER_COLUMNS, to_public_user

CONSENT_COLUMNS = (
    "id,status,requested_at,responded_at,expires_at,used_at,requester_id,consenter_id,"
    f"requester:users!scam_consents_requester_id_fkey({PUBLIC_USER_COLUMNS}),"
    # The consenter's voice status decides whether the consent is usable.
    f"consenter:users!scam_consents_consenter_id_fkey({PUBLIC_USER_COLUMNS},voice:voices(status))"
)

# Voice statuses in which the voice can be used in a call. Revisit once the
# voice pipeline decides what "ready" means for zero-shot cloning.
USABLE_VOICE_STATUSES = frozenset({"ready"})

# PostgreSQL reads the timestamp `now` as the current time on the database.
DATABASE_NOW = "now"

type ConsentProblem = Literal["consent_unusable", "voice_not_ready"]


class ConsentNotUsableError(Exception):
    def __init__(self, code: ConsentProblem, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def usable_filters(requester_id: UUID) -> dict[str, str]:
    """PostgREST filters for the requester's accepted, unused, unexpired consents.

    Revoked consents have their own status, so they never match. The voice is
    checked separately.
    """
    return {
        "requester_id": f"eq.{requester_id}",
        "status": "eq.accepted",
        "used_at": "is.null",
        "expires_at": f"gt.{DATABASE_NOW}",
    }


def is_usable(row: dict[str, Any], user_id: UUID, now: datetime) -> bool:
    """Whether `user_id` can start a call with this consent row (CONSENT_COLUMNS)."""
    return (
        row["requester_id"] == str(user_id)
        and row["status"] == "accepted"
        and row["used_at"] is None
        and row["expires_at"] is not None
        and datetime.fromisoformat(row["expires_at"]) > now
        and _embedded_voice_status(row["consenter"]) in USABLE_VOICE_STATUSES
    )


def _embedded_voice_status(user: dict[str, Any]) -> str | None:
    voice = user.get("voice")
    # One-to-one (voices.user_id is unique), so PostgREST embeds an object or
    # null; accept a one-item list too.
    if isinstance(voice, list):
        voice = voice[0] if voice else None
    return voice.get("status") if isinstance(voice, dict) else None


async def voice_is_ready(db: SupabaseDatabase, user_id: UUID) -> bool:
    rows = await db.select("voices", {"user_id": f"eq.{user_id}"}, "status")
    return bool(rows) and rows[0]["status"] in USABLE_VOICE_STATUSES


async def use_consent(
    db: SupabaseDatabase, consent_id: UUID, requester_id: UUID
) -> dict[str, Any]:
    """Check that a consent can start a call, then mark it used.

    For the calls module: call this when a call starts. Marking the consent
    used is a single conditional update, so two calls started at the same time
    cannot both use it, and a consent revoked, expired or deleted (unfriended)
    meanwhile cannot be used. Raises ConsentNotUsableError if it cannot be used.
    Returns the consent row (CONSENT_COLUMNS) after marking it used.
    """
    rows = await db.select(
        "scam_consents",
        {"id": f"eq.{consent_id}", **usable_filters(requester_id)},
        "id,consenter_id",
    )
    if not rows:
        raise ConsentNotUsableError(
            "consent_unusable",
            "This consent does not exist, is not accepted, was already used or expired.",
        )
    if not await voice_is_ready(db, UUID(rows[0]["consenter_id"])):
        raise ConsentNotUsableError("voice_not_ready", "This user's voice is not ready.")

    used = await db.update(
        "scam_consents",
        {"id": f"eq.{consent_id}", **usable_filters(requester_id)},
        {"used_at": DATABASE_NOW},
        CONSENT_COLUMNS,
    )
    if not used:
        # Used, revoked, expired or unfriended since the check above.
        raise ConsentNotUsableError(
            "consent_unusable", "This consent was already used or is no longer valid."
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
        usable=is_usable(row, user_id, now),
    )
