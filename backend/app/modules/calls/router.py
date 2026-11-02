from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from app.api.schemas import Page, Pagination
from app.core.errors import not_implemented
from app.modules.calls.schemas import CallCreate, CallDetailResponse, CallResponse

router = APIRouter(prefix="/calls", tags=["Calls"])


@router.post("", response_model=CallResponse, status_code=202)
async def start_call(payload: CallCreate) -> CallResponse:
    not_implemented("Call initiation")


@router.get("", response_model=Page[CallResponse])
async def get_call_history(
    pagination: Annotated[Pagination, Query()],
) -> Page[CallResponse]:
    not_implemented("Call history")


@router.get("/{id}", response_model=CallDetailResponse)
async def get_call_details(id: UUID) -> CallDetailResponse:
    not_implemented("Call details")
