"""Check an uploaded voice sample from its contents and measure its length.

Only PCM WAV is accepted for now, read with the standard library: it carries
no compressed metadata, so parsing it never uses more memory than the upload
itself, and the declared length can be checked against the audio actually
present. The client's file name and content type are ignored.

TBD with the frontend and the voice pipeline: whether to accept other formats.
Browsers can record WAV (for example with an AudioWorklet); MediaRecorder's
default WebM/Opus would need converting.
"""

import io
import wave
from dataclasses import dataclass

EXTENSION = "wav"
CONTENT_TYPE = "audio/wav"
SUPPORTED_FORMATS = "PCM WAV"
MIN_SAMPLE_RATE = 8_000
MAX_SAMPLE_RATE = 48_000


@dataclass(frozen=True)
class AudioInfo:
    extension: str
    content_type: str
    duration_seconds: float


def inspect_audio(data: bytes) -> AudioInfo | None:
    """Return the file's format and length, or None if it is not complete PCM WAV audio."""
    try:
        with wave.open(io.BytesIO(data), "rb") as audio:
            channels = audio.getnchannels()
            sample_width = audio.getsampwidth()
            rate = audio.getframerate()
            frames = audio.getnframes()
            payload = audio.readframes(frames)
    except (wave.Error, EOFError, ValueError):
        return None
    if not (
        1 <= channels <= 2
        and 1 <= sample_width <= 4
        and MIN_SAMPLE_RATE <= rate <= MAX_SAMPLE_RATE
        and frames > 0
    ):
        return None
    # The header's frame count must match the audio that is actually there.
    if len(payload) != frames * channels * sample_width:
        return None
    return AudioInfo(EXTENSION, CONTENT_TYPE, frames / rate)
