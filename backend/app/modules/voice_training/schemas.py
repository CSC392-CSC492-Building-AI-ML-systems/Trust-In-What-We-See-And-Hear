from uuid import UUID

from pydantic import BaseModel, Field


class VoiceTrainingJobResponse(BaseModel):
    id: UUID
    status: str = Field(description="Job status. Values TBD.")
