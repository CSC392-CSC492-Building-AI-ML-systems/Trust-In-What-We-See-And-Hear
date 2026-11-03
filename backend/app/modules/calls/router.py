from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from app.api.schemas import Page, Pagination
from app.core.errors import NOT_FOUND_RESPONSES, not_implemented
from app.modules.calls.schemas import CallCreate, CallDetailResponse, CallResponse

router = APIRouter(prefix="/calls", tags=["Calls"])


@router.post("", response_model=CallResponse, status_code=202)
async def start_call(payload: CallCreate) -> CallResponse:
    """Start a scam call to the current user. Check progress with `GET /calls/{id}`.

    **TBD:** whether a consent can be used for more than one call.
    """
    not_implemented("Call initiation")


@router.get("", response_model=Page[CallResponse])
async def get_call_history(
    pagination: Annotated[Pagination, Query()],
) -> Page[CallResponse]:
    """List the current user's calls."""
    not_implemented("Call history")


@router.get("/{id}", response_model=CallDetailResponse, responses=NOT_FOUND_RESPONSES)
async def get_call_details(id: UUID) -> CallDetailResponse:
    """Return a call's details, including its result summary once available."""
    not_implemented("Call details")
