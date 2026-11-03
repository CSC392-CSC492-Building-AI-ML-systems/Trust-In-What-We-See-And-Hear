from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from app.api.schemas import Page, Pagination
from app.core.errors import NOT_FOUND_RESPONSES, not_implemented
from app.modules.consents.schemas import ConsentRequestCreate, ConsentResponse

router = APIRouter(prefix="/consents", tags=["Consents"])


@router.post("", response_model=ConsentResponse, status_code=201)
async def request_consent(payload: ConsentRequestCreate) -> ConsentResponse:
    """Ask a friend for consent to use their profile and voice in a scam call to the current user."""
    not_implemented("Consent requests")


@router.get("", response_model=Page[ConsentResponse])
async def get_consents(
    pagination: Annotated[Pagination, Query()],
) -> Page[ConsentResponse]:
    """List accepted, unexpired consents requested by the current user.

    These are the consents that can be used to start a call.
    """
    not_implemented("Consent listing")


@router.post(
    "/{id}/accept", response_model=ConsentResponse, responses=NOT_FOUND_RESPONSES
)
async def accept_consent(id: UUID) -> ConsentResponse:
    """Accept a consent request sent to the current user.

    The requester can use the consent until `expires_at`.

    **TBD:** the expiry period, and how users find consent requests sent to them.
    """
    not_implemented("Consent acceptance")


@router.post(
    "/{id}/decline", response_model=ConsentResponse, responses=NOT_FOUND_RESPONSES
)
async def decline_consent(id: UUID) -> ConsentResponse:
    """Decline a consent request sent to the current user."""
    not_implemented("Consent decline")
