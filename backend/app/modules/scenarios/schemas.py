from uuid import UUID

from pydantic import BaseModel, JsonValue

from app.api.schemas import RequestModel


class ScenarioCreate(RequestModel):
    content: dict[str, JsonValue]


class ScenarioUpdate(RequestModel):
    content: dict[str, JsonValue]


class ScenarioResponse(BaseModel):
    id: UUID
    creator_user_id: UUID
    content: dict[str, JsonValue]
