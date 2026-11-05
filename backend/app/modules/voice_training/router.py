from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, UploadFile

from app.core.errors import NOT_FOUND_RESPONSES, not_implemented
from app.modules.voice_training.schemas import VoiceTrainingJobResponse

router = APIRouter(prefix="/voice-training/jobs", tags=["Voice training"])


@router.post("", response_model=VoiceTrainingJobResponse, status_code=202)
async def train_voice(
    audio: Annotated[
        UploadFile,
        File(description="MP3 voice sample."),
    ],
) -> VoiceTrainingJobResponse:
    """Start training the current user's voice model.

    An external Chatterbox job produces a `.pt` file, stored privately in
    Supabase and linked to the user. Check progress with
    `GET /voice-training/jobs/{id}`.
    """
    not_implemented("Voice training")


@router.get(
    "/{id}", response_model=VoiceTrainingJobResponse, responses=NOT_FOUND_RESPONSES
)
async def get_voice_training_job(id: UUID) -> VoiceTrainingJobResponse:
    not_implemented("Voice training job status")
