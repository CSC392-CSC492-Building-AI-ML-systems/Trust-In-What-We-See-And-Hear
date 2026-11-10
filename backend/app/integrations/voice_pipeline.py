"""Hand-off point to the self-hosted voice pipeline (Chatterbox).

Chatterbox is pretrained and clones a voice from a reference recording, so
there is no per-user training. What the pipeline needs from a new sample is
TBD: it may read the sample at call time, or derive a file from it once
(stored in the voice-models bucket) and then set the voice to `ready`.
"""

from uuid import UUID


async def on_sample_uploaded(voice_id: UUID, user_id: UUID, sample_path: str) -> None:
    """Called after a user's reference sample is stored and recorded.

    TODO(voice pipeline): start processing here, or check the recording
    matches the enrollment script. Until then the voice stays `uploaded`.
    """
