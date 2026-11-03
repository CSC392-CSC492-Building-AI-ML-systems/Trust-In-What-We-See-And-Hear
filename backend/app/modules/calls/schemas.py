from uuid import UUID

from pydantic import BaseModel, Field, JsonValue

from app.api.schemas import RequestModel


class CallCreate(RequestModel):
    consent_id: UUID = Field(
        description="An accepted, unexpired consent from `GET /consents`."
    )
    scenario_id: UUID


class CallResponse(BaseModel):
    id: UUID
    consent_id: UUID
    scenario_id: UUID
    status: str = Field(description="Call status. Values TBD.")


class CallDetailResponse(CallResponse):
    summary: dict[str, JsonValue] | None = Field(
        default=None, description="Call result summary, once available. Format TBD."
    )
