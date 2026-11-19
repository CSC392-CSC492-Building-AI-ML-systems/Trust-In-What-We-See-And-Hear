"""Call rules shared by the public and internal call routes.

A call's life (voice-pipeline/BACKEND.md):

1. `POST /calls` checks the scenario and consent, builds and validates the
   call context, uses up the consent, and creates the call as `queued` with a
   one-time token. The browser gets the token inside `ws_url`.
2. The browser connects to the voice pipeline, which claims the call with the
   token (`POST /internal/calls/{id}/claim`). The call becomes `in_progress`.
3. When the call ends, the pipeline reports how it ended and its score
   (`POST /internal/calls/{id}/result`). The call becomes `completed`, or
   `failed` if it ended in an error or could not be scored.

A call whose token is never claimed, or that never gets a result, is failed
with `timed_out` the next time the user reads their calls.

AI-assisted code (written with Claude Code), cited per the CSC398 AI policy.
"""

import hashlib
import logging
import secrets
from datetime import timedelta
from typing import Any
from uuid import UUID

from app.core import clock
from app.core.config import Settings
from app.integrations.supabase import SupabaseDatabase
from app.modules.calls.context import CallContext, CallContextValidationError
from app.modules.calls.schemas import CallResponse, CallSummary
from app.modules.consents.service import CONSENT_COLUMNS
from app.modules.friends.service import own_notes

logger = logging.getLogger(__name__)

RESULT_COLUMNS = (
    "outcome,score,summary,did_well,to_improve,red_flags_noticed,red_flags_missed,"
    "sensitive_info_shared"
)
CALL_COLUMNS = (
    "id,consent_id,scenario_id,persona_name,scenario_title,status,created_at,"
    f"started_at,ended_at,duration_seconds,failure_reason,result:call_results({RESULT_COLUMNS})"
)

CALL_CONSENT_COLUMNS = (
    f"{CONSENT_COLUMNS},"
    # For the target's note about the persona (requester_id is the target).
    "friendship:friendships(requester_id,requester_notes,addressee_notes)"
)
# Shown to the caller as "- <key>: <note>"; at most 30 characters.
TARGET_NOTES_KEY = "What they know about you"

# Values of calls.failure_reason.
TIMED_OUT = "timed_out"  # token never claimed in time, or no result arrived
ENDED_BY_ERROR = "error"  # the pipeline ended the call with an error
NOT_SCORED = "not_scored"  # the call ended but has no result
VOICE_UNAVAILABLE = "voice_unavailable"  # the persona's voice was gone at claim time

FINISHED_STATUSES = frozenset({"completed", "failed", "canceled"})


class CallContextError(Exception):
    """The call context could not be built; the call must not start."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def pipeline_secret(settings: Settings) -> str:
    """The shared secret, or "" when it is not set (an empty env var gives "")."""
    secret = settings.pipeline_backend_secret
    return secret.get_secret_value() if secret is not None else ""


def new_claim_token() -> tuple[str, str]:
    """A random one-time token and the hash of it that is stored."""
    token = secrets.token_urlsafe(32)
    return token, hash_claim_token(token)


def hash_claim_token(token: str) -> str:
    # The token has 256 random bits, so a plain SHA-256 is enough; no salt needed.
    return hashlib.sha256(token.encode()).hexdigest()


def full_name(user: dict[str, Any]) -> str:
    return f"{user['first_name']} {user['last_name']}".strip()


def build_context(
    consent: dict[str, Any], scenario: dict[str, Any]
) -> CallContext:
    """The validated call context for a consent row (CALL_CONSENT_COLUMNS) and scenario row.

    `details` holds only the target's own note about the persona (friends'
    notes), so the caller knows what the target knows about them. The note is
    written by the person being called, never by the persona. Onboarding
    answers are not used: whose may be used is not decided (FOLLOW_UPS.md).
    """
    if not scenario.get("ask"):
        raise CallContextError(
            "scenario_incomplete",
            "This scenario has no request for the caller to make. Add one, then try again.",
        )
    target = consent["requester"]
    details = {}
    friendship = consent.get("friendship")
    if isinstance(friendship, dict):
        note = own_notes(friendship, consent["requester_id"])
        if note:
            details[TARGET_NOTES_KEY] = note
    data = {
        "persona": {"name": full_name(consent["consenter"]), "relationship": "friend"},
        "target": {"name": full_name(target), "called_as": target["first_name"]},
        "scenario": {
            "title": scenario["title"],
            "situation": scenario["description"],
            "ask": scenario["ask"],
            "tactics": scenario["red_flags"],
        },
        "details": details,
    }
    try:
        context, warnings = CallContext.from_dict(data)
    except CallContextValidationError as exc:
        # Paths and codes only: issues never contain the rejected values.
        logger.warning(
            "Call context rejected: %s", [(i.path, i.code) for i in exc.errors]
        )
        raise CallContextError(
            "call_context_invalid",
            "This scenario or profile cannot be used for a call. Check the scenario's text.",
        ) from exc
    if warnings:
        logger.info("Call context warnings: %s", [(i.path, i.code) for i in warnings])
    return context


async def fail_stale_calls(
    db: SupabaseDatabase, user_id: UUID, result_timeout_seconds: int
) -> None:
    """Fail the user's calls that were never claimed in time or never got a result.

    The pipeline sends no result if it dies mid-call, and nothing else would
    end such a call, so this runs whenever the user reads their calls.
    """
    started_before = clock.to_iso(clock.now() - timedelta(seconds=result_timeout_seconds))
    await db.update(
        "calls",
        {
            "user_id": f"eq.{user_id}",
            "or": (
                "(and(status.eq.queued,claim_expires_at.lt.now),"
                # Quoted: `.` and `:` are reserved inside PostgREST's or=(...).
                f'and(status.eq.in_progress,started_at.lt."{started_before}"))'
            ),
        },
        {
            "status": "failed",
            "failure_reason": TIMED_OUT,
            "ended_at": "now",
            "claim_token_hash": None,
            "call_context": None,
        },
        "id",
    )


def embedded_result(row: dict[str, Any]) -> dict[str, Any] | None:
    result = row.get("result")
    # call_results.call_id is the primary key, so PostgREST embeds an object or
    # null; accept a one-item list too.
    if isinstance(result, list):
        result = result[0] if result else None
    return result if isinstance(result, dict) else None


def shown_score(result: dict[str, Any]) -> int | None:
    # An inconclusive call (the user said nothing) is scored 0, which means nothing.
    return None if result["outcome"] == "inconclusive" else result["score"]


def call_fields(row: dict[str, Any]) -> dict[str, Any]:
    """CallResponse fields from a CALL_COLUMNS row."""
    result = embedded_result(row) if row["status"] == "completed" else None
    return {
        "id": row["id"],
        "consent_id": row["consent_id"],
        "scenario_id": row["scenario_id"],
        "persona_name": row["persona_name"],
        "scenario_title": row["scenario_title"],
        "status": row["status"],
        "outcome": result["outcome"] if result else None,
        "score": shown_score(result) if result else None,
        "created_at": row["created_at"],
        "started_at": row["started_at"],
        "ended_at": row["ended_at"],
        "duration_seconds": row["duration_seconds"],
    }


def to_call(row: dict[str, Any]) -> CallResponse:
    return CallResponse(**call_fields(row))


def to_summary(row: dict[str, Any]) -> CallSummary | None:
    result = embedded_result(row) if row["status"] == "completed" else None
    if result is None:
        return None
    return CallSummary(
        **{**result, "score": shown_score(result)},
        duration_seconds=row["duration_seconds"],
    )
