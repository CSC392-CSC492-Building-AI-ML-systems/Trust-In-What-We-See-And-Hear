"""Decode an uploaded voice sample with ffmpeg and convert it to one stored format.

Browsers record WebM/Opus (Chrome, Firefox) or MP4/AAC (Safari), so those are
accepted along with Ogg, MP3, FLAC and WAV. A compressed file's header can
claim any length, so the only trustworthy length is the audio ffmpeg actually
decodes. Every sample is stored as mono 16-bit PCM WAV at one sample rate, so
the voice pipeline only ever reads one format.

The upload is untrusted, so ffmpeg runs as a separate process with:

- only local file input (an HLS playlist, for example, could otherwise make it
  read other files or fetch URLs), and only the containers and audio decoders
  browsers produce;
- a time limit, at most `MAX_CONCURRENT_DECODES` at once, and no single
  allocation over `MAX_ALLOCATION_BYTES`;
- output stopped just past the longest allowed length, so a long file cannot
  produce a large result.

The client's file name and content type are ignored.
"""

import io
import logging
import subprocess
import tempfile
import threading
import wave
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

EXTENSION = "wav"
CONTENT_TYPE = "audio/wav"
SUPPORTED_FORMATS = "WebM, MP4/M4A, Ogg, MP3, FLAC or WAV"

# ffmpeg demuxer and decoder names.
ALLOWED_CONTAINERS = "matroska,mov,ogg,mp3,flac,wav"
ALLOWED_DECODERS = (
    "opus,libopus,vorbis,libvorbis,aac,aac_fixed,mp3,mp3float,flac,"
    "pcm_u8,pcm_s16le,pcm_s24le,pcm_s32le,pcm_f32le,pcm_f64le"
)
MAX_CONCURRENT_DECODES = 2
# How long an upload waits for a free decoder before giving up.
DECODER_WAIT_SECONDS = 10
DECODE_TIMEOUT_SECONDS = 20
MAX_ALLOCATION_BYTES = 64 * 1024 * 1024

_decoders = threading.BoundedSemaphore(MAX_CONCURRENT_DECODES)


class AudioProcessingUnavailableError(Exception):
    """ffmpeg is missing or busy, so the file could not be checked."""


@dataclass(frozen=True)
class DecodedAudio:
    data: bytes  # mono 16-bit PCM WAV
    extension: str
    content_type: str
    duration_seconds: float


def decode_audio(
    data: bytes, *, ffmpeg_path: str, sample_rate: int, max_seconds: float
) -> DecodedAudio | None:
    """Decode the upload to mono WAV, or return None if it is not supported audio.

    Blocking: call it from a worker thread. Raises
    AudioProcessingUnavailableError if ffmpeg cannot be run.
    """
    if not _decoders.acquire(timeout=DECODER_WAIT_SECONDS):
        raise AudioProcessingUnavailableError("All audio decoders are busy.")
    try:
        with tempfile.TemporaryDirectory(prefix="voice-") as folder:
            source = Path(folder) / "upload"
            target = Path(folder) / "decoded.wav"
            source.write_bytes(data)
            if not _run_ffmpeg(ffmpeg_path, source, target, sample_rate, max_seconds):
                return None
            return _read_decoded(target.read_bytes())
    finally:
        _decoders.release()


def _run_ffmpeg(
    ffmpeg_path: str, source: Path, target: Path, sample_rate: int, max_seconds: float
) -> bool:
    command = [
        ffmpeg_path, "-nostdin", "-hide_banner", "-loglevel", "error",
        "-max_alloc", str(MAX_ALLOCATION_BYTES), "-threads", "1",
        # Input options: what ffmpeg may open and decode.
        "-protocol_whitelist", "file",
        "-format_whitelist", ALLOWED_CONTAINERS,
        "-codec_whitelist", ALLOWED_DECODERS,
        "-i", str(source),
        # Output: the first audio stream only, with no metadata, cut just past
        # the maximum so an overlong file is still reported as too long.
        "-map", "0:a:0", "-map_metadata", "-1", "-fflags", "+bitexact",
        "-t", str(max_seconds + 1),
        "-ac", "1", "-ar", str(sample_rate), "-c:a", "pcm_s16le", "-f", "wav",
        str(target),
    ]  # fmt: skip
    try:
        result = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=DECODE_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError as exc:
        raise AudioProcessingUnavailableError(f"ffmpeg not found at {ffmpeg_path!r}.") from exc
    except subprocess.TimeoutExpired:
        logger.warning("ffmpeg took over %s seconds; rejecting the upload.", DECODE_TIMEOUT_SECONDS)
        return False
    if result.returncode != 0:
        logger.info("ffmpeg rejected an upload: %s", result.stderr[-500:].decode(errors="replace"))
        return False
    return target.exists()


def _read_decoded(output: bytes) -> DecodedAudio | None:
    with wave.open(io.BytesIO(output), "rb") as audio:
        frames = audio.getnframes()
        rate = audio.getframerate()
    if frames == 0:
        return None
    return DecodedAudio(output, EXTENSION, CONTENT_TYPE, frames / rate)
