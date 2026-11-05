from uuid import UUID

from pydantic import BaseModel, Field, JsonValue

from app.api.schemas import RequestModel

CONTENT_DESCRIPTION = "Scenario content. Format TBD."


class ScenarioCreate(RequestModel):
    content: dict[str, JsonValue] = Field(description=CONTENT_DESCRIPTION)


class ScenarioUpdate(RequestModel):
    content: dict[str, JsonValue] = Field(description=CONTENT_DESCRIPTION)


class ScenarioResponse(BaseModel):
    id: UUID
    creator_user_id: UUID
    content: dict[str, JsonValue] = Field(description=CONTENT_DESCRIPTION)
