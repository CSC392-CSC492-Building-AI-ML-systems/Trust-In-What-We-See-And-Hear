"""Identify an uploaded audio file from its contents and measure its length.

The client's file name and content type are ignored: only the bytes decide
the format.
"""

import io
import math
from dataclasses import dataclass

import mutagen
from mutagen.flac import FLAC
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4
from mutagen.oggopus import OggOpus
from mutagen.oggvorbis import OggVorbis
from mutagen.wave import WAVE

# Format -> (file extension, content type). WebM, which Chrome's MediaRecorder
# produces, is not supported yet: mutagen cannot read its length. TBD with the
# frontend and the voice pipeline whether the browser records one of these
# formats or the backend converts WebM.
FORMATS: dict[type, tuple[str, str]] = {
    MP3: ("mp3", "audio/mpeg"),
    WAVE: ("wav", "audio/wav"),
    OggOpus: ("ogg", "audio/ogg"),
    OggVorbis: ("ogg", "audio/ogg"),
    FLAC: ("flac", "audio/flac"),
    MP4: ("m4a", "audio/mp4"),
}
SUPPORTED_FORMATS = "MP3, WAV, Ogg (Opus or Vorbis), FLAC or M4A"


@dataclass(frozen=True)
class AudioInfo:
    extension: str
    content_type: str
    duration_seconds: float


def inspect_audio(data: bytes) -> AudioInfo | None:
    """Return the file's format and length, or None if it is not supported audio."""
    try:
        audio = mutagen.File(io.BytesIO(data), options=list(FORMATS))
    except Exception:
        # Parsers can fail in many ways on malformed or hostile input.
        return None
    if audio is None or type(audio) not in FORMATS:
        return None
    duration = getattr(audio.info, "length", None)
    if not isinstance(duration, int | float) or not math.isfinite(duration) or duration <= 0:
        return None
    extension, content_type = FORMATS[type(audio)]
    return AudioInfo(extension, content_type, float(duration))
