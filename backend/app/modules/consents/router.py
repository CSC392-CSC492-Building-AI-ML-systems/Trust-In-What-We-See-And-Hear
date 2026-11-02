from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from app.api.schemas import Page, Pagination
from app.core.errors import not_implemented
from app.modules.consents.schemas import ConsentRequestCreate, ConsentResponse

router = APIRouter(prefix="/consents", tags=["Consents"])


@router.post("", response_model=ConsentResponse, status_code=201)
async def request_consent(payload: ConsentRequestCreate) -> ConsentResponse:
    not_implemented("Consent requests")


@router.get("", response_model=Page[ConsentResponse])
async def get_consents(
    pagination: Annotated[Pagination, Query()],
) -> Page[ConsentResponse]:
    """List usable accepted consents requested by the current user."""
    not_implemented("Consent listing")


@router.post("/{id}/accept", response_model=ConsentResponse)
async def accept_consent(id: UUID) -> ConsentResponse:
    not_implemented("Consent acceptance")


@router.post("/{id}/decline", response_model=ConsentResponse)
async def decline_consent(id: UUID) -> ConsentResponse:
    not_implemented("Consent decline")
