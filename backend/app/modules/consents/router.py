from datetime import timedelta
from typing import Annotated, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status

from app.api.schemas import Page, Pagination
from app.core import clock
from app.core.auth import AuthenticatedUser, get_current_user
from app.core.errors import api_error, error_responses, merge_responses
from app.integrations.supabase import (
    SupabaseConflictError,
    SupabaseDatabase,
    SupabaseForeignKeyError,
    get_supabase_db,
)
from app.modules.consents.schemas import (
    ConsentRequestCreate,
    ConsentRequestQuery,
    ConsentResponse,
)
from app.modules.consents.service import (
    CONSENT_COLUMNS,
    DATABASE_NOW,
    to_consent,
    usable_filters,
    voice_is_ready,
)
from app.modules.friends.service import find_friendship

router = APIRouter(prefix="/consents", tags=["Consents"])

CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_user)]
SupabaseDb = Annotated[SupabaseDatabase, Depends(get_supabase_db)]

ONE_PENDING_CONSTRAINT = "scam_consents_one_pending_idx"

NOT_FRIENDS = ("not_friends", "The two users are not friends.")
PENDING_NOT_FOUND = error_responses(
    status.HTTP_404_NOT_FOUND,
    ("consent_not_found", "No pending consent request with this ID was sent to you."),
)


@router.post(
    "",
    response_model=ConsentResponse,
    status_code=status.HTTP_201_CREATED,
    responses=merge_responses(
        error_responses(
            status.HTTP_400_BAD_REQUEST,
            ("cannot_request_self", "The consenter is the current user."),
        ),
        error_responses(
            status.HTTP_409_CONFLICT,
            NOT_FRIENDS,
            ("voice_not_ready", "The friend's voice is not ready to use."),
            ("consent_request_pending", "A request to this friend is already pending."),
            ("consent_available", "An accepted, unused consent from this friend exists."),
        ),
    ),
)
async def request_consent(
    payload: ConsentRequestCreate, user: CurrentUser, supabase_db: SupabaseDb
) -> ConsentResponse:
    """Ask a friend for consent to use their profile and voice in one call to the current user."""
    consenter_id = payload.consenter_user_id
    if consenter_id == user.user_id:
        api_error(
            status.HTTP_400_BAD_REQUEST,
            "cannot_request_self",
            "You cannot request consent from yourself.",
        )
    friendship = await find_friendship(supabase_db, user.user_id, consenter_id)
    if friendship is None or friendship["status"] != "accepted":
        _not_friends()
    if not await voice_is_ready(supabase_db, consenter_id):
        api_error(
            status.HTTP_409_CONFLICT,
            "voice_not_ready",
            "This friend's voice is not ready to use yet.",
        )
    available = await supabase_db.select(
        "scam_consents",
        {**usable_filters(user.user_id), "consenter_id": f"eq.{consenter_id}"},
        "id",
    )
    if available:
        api_error(
            status.HTTP_409_CONFLICT,
            "consent_available",
            "You already have an unused consent from this friend.",
        )
    try:
        created = await supabase_db.insert(
            "scam_consents",
            {
                "requester_id": str(user.user_id),
                "consenter_id": str(consenter_id),
                # Deleted with the friendship, so it cannot outlive it.
                "friendship_id": friendship["id"],
            },
            CONSENT_COLUMNS,
        )
    except SupabaseForeignKeyError:
        # The friendship was removed since the check above.
        _not_friends()
    except SupabaseConflictError as exc:
        if exc.constraint == ONE_PENDING_CONSTRAINT:
            api_error(
                status.HTTP_409_CONFLICT,
                "consent_request_pending",
                "You already have a pending request to this friend.",
            )
        raise
    return to_consent(created, user.user_id, clock.now())


@router.get("", response_model=Page[ConsentResponse])
async def get_consents(
    pagination: Annotated[Pagination, Query()], user: CurrentUser, supabase_db: SupabaseDb
) -> Page[ConsentResponse]:
    """List the current user's accepted, unused, unexpired consents, soonest to expire first.

    `usable` is false for one whose friend's voice is not ready; starting a call
    with it fails until the voice is ready again.
    """
    now = clock.now()
    rows = await supabase_db.select(
        "scam_consents",
        usable_filters(user.user_id),
        CONSENT_COLUMNS,
        order="expires_at.asc,id",
        limit=pagination.limit,
        offset=pagination.offset,
    )
    return Page(
        items=[to_consent(row, user.user_id, now) for row in rows],
        limit=pagination.limit,
        offset=pagination.offset,
    )


@router.get("/requests", response_model=Page[ConsentResponse])
async def get_consent_requests(
    pagination: Annotated[ConsentRequestQuery, Query()],
    user: CurrentUser,
    supabase_db: SupabaseDb,
) -> Page[ConsentResponse]:
    """List pending consent requests, newest first."""
    direction = pagination.direction
    if direction == "incoming":
        filters = {"consenter_id": f"eq.{user.user_id}"}
    elif direction == "outgoing":
        filters = {"requester_id": f"eq.{user.user_id}"}
    else:
        filters = {"or": f"(requester_id.eq.{user.user_id},consenter_id.eq.{user.user_id})"}
    now = clock.now()
    rows = await supabase_db.select(
        "scam_consents",
        {**filters, "status": "eq.pending"},
        CONSENT_COLUMNS,
        order="requested_at.desc,id",
        limit=pagination.limit,
        offset=pagination.offset,
    )
    return Page(
        items=[to_consent(row, user.user_id, now) for row in rows],
        limit=pagination.limit,
        offset=pagination.offset,
    )


@router.post(
    "/{id}/accept",
    response_model=ConsentResponse,
    responses=PENDING_NOT_FOUND,
)
async def accept_consent(
    id: UUID, request: Request, user: CurrentUser, supabase_db: SupabaseDb
) -> ConsentResponse:
    """Accept a consent request sent to the current user.

    The requester can then use it for one call until `expires_at`. A consent
    only exists while the two users are friends, so no friendship check is needed.
    """
    now = clock.now()
    valid_for = timedelta(minutes=request.app.state.settings.consent_valid_minutes)
    rows = await supabase_db.update(
        "scam_consents",
        _pending_for(id, user.user_id),
        {
            "status": "accepted",
            "responded_at": clock.to_iso(now),
            "expires_at": clock.to_iso(now + valid_for),
        },
        CONSENT_COLUMNS,
    )
    if not rows:
        _pending_not_found()
    return to_consent(rows[0], user.user_id, now)


@router.post("/{id}/decline", response_model=ConsentResponse, responses=PENDING_NOT_FOUND)
async def decline_consent(id: UUID, user: CurrentUser, supabase_db: SupabaseDb) -> ConsentResponse:
    """Decline a consent request sent to the current user."""
    now = clock.now()
    rows = await supabase_db.update(
        "scam_consents",
        _pending_for(id, user.user_id),
        {"status": "declined", "responded_at": clock.to_iso(now)},
        CONSENT_COLUMNS,
    )
    if not rows:
        _pending_not_found()
    return to_consent(rows[0], user.user_id, now)


@router.post(
    "/{id}/revoke",
    response_model=ConsentResponse,
    responses=error_responses(
        status.HTTP_404_NOT_FOUND,
        ("consent_not_found", "No accepted, unused, unexpired consent you gave has this ID."),
    ),
)
async def revoke_consent(id: UUID, user: CurrentUser, supabase_db: SupabaseDb) -> ConsentResponse:
    """Withdraw a consent the current user gave, before it is used."""
    now = clock.now()
    rows = await supabase_db.update(
        "scam_consents",
        {
            "id": f"eq.{id}",
            "consenter_id": f"eq.{user.user_id}",
            "status": "eq.accepted",
            "used_at": "is.null",
            "expires_at": f"gt.{DATABASE_NOW}",
        },
        {"status": "revoked"},
        CONSENT_COLUMNS,
    )
    if not rows:
        api_error(
            status.HTTP_404_NOT_FOUND,
            "consent_not_found",
            "No consent you gave with that ID can be revoked.",
        )
    return to_consent(rows[0], user.user_id, now)


def _pending_for(consent_id: UUID, consenter_id: UUID) -> dict[str, str]:
    return {
        "id": f"eq.{consent_id}",
        "consenter_id": f"eq.{consenter_id}",
        "status": "eq.pending",
    }


def _pending_not_found() -> NoReturn:
    api_error(
        status.HTTP_404_NOT_FOUND,
        "consent_not_found",
        "No pending consent request with that ID was sent to you.",
    )


def _not_friends() -> NoReturn:
    api_error(status.HTTP_409_CONFLICT, "not_friends", "You are not friends with this user.")
