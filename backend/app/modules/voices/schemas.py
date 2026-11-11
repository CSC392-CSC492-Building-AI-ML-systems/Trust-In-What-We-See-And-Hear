"""Response models for the voice endpoints.

AI-assisted code (written with Claude Code), cited per the CSC398 AI policy.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

VoiceStatus = Literal["uploaded", "training", "ready", "failed"]


class VoiceResponse(BaseModel):
    """The current user's voice. Never includes where the files are stored."""

    status: VoiceStatus = Field(
        description=(
            "`uploaded`: the sample is stored. `training`: the voice pipeline is "
            "processing it. `ready`: friends can use it in calls. `failed`: see "
            "`error_message` and upload a new sample.\n\n"
            "**TBD:** whether `training` is needed, since Chatterbox clones from "
            "the reference sample without per-user training."
        )
    )
    error_message: str | None
    sample_duration_seconds: float | None
    owner_consent_at: datetime = Field(
        description="When the user confirmed the voice is theirs and agreed to it being cloned."
    )
    updated_at: datetime
