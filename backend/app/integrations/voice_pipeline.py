"""Hand-off point to the self-hosted voice pipeline (Chatterbox).

Chatterbox is pretrained and clones a voice from a reference recording, so
there is no per-user training: the pipeline reads the stored sample (mono WAV)
when a call starts, and an accepted upload is `ready` at once. Nothing is
derived from the sample ahead of time, so the voice-models bucket is unused
for now.

AI-assisted code (written with Claude Code), cited per the CSC398 AI policy.
"""

from uuid import UUID


async def on_sample_uploaded(voice_id: UUID, user_id: UUID, sample_path: str) -> None:
    """Called after a user's reference sample is stored and recorded as `ready`.

    TODO(voice pipeline): for example trim leading silence, or check the
    recording matches the enrollment script (and set `failed` if not).
    """
