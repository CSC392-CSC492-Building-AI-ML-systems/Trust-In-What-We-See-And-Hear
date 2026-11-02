from uuid import UUID

from pydantic import BaseModel, JsonValue

from app.api.schemas import RequestModel


class CallCreate(RequestModel):
    consent_id: UUID
    scenario_id: UUID


class CallResponse(BaseModel):
    id: UUID
    consent_id: UUID
    scenario_id: UUID
    status: str


class CallDetailResponse(CallResponse):
    summary: dict[str, JsonValue] | None = None
