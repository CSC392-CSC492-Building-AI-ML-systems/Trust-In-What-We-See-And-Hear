from uuid import UUID

from pydantic import BaseModel


class VoiceTrainingJobResponse(BaseModel):
    id: UUID
    status: str
