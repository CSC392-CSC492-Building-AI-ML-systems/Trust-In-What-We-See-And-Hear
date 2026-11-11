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
- at most `MAX_STREAMS` streams, no single allocation over
  `MAX_ALLOCATION_BYTES` and, on Linux, at most `MAX_MEMORY_BYTES` of memory
  in all (crafted Matroska files can otherwise make each stream expand
  compressed data);
- output stopped just past the longest allowed length and capped in bytes too,
  since a crafted file can repeat timestamps so that `-t` never triggers;
- a time limit, and at most `MAX_CONCURRENT_DECODES` at once. Uploads wait for
  a free decoder without holding a worker thread.

The client's file name and content type are ignored.
"""

import io
import logging
import shutil
import subprocess
import sys
import tempfile
import threading
import wave
from collections import deque
from dataclasses import dataclass
from functools import partial
from pathlib import Path

import anyio
import anyio.to_thread

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
MAX_STREAMS = 2  # audio, plus video if the recording has it
MAX_ALLOCATION_BYTES = 16 * 1024 * 1024
# Virtual memory, so it includes ffmpeg's shared libraries (about 256 MiB for a
# typical Linux build). Enforced on Linux only; Windows and macOS are only used
# for development.
MAX_MEMORY_BYTES = 1024 * 1024 * 1024
MAX_CONCURRENT_DECODES = 2
# How long an upload waits for a free decoder before giving up.
DECODER_WAIT_SECONDS = 10
DECODE_TIMEOUT_SECONDS = 20
OUTPUT_SAMPLE_BYTES = 2  # 16-bit mono
WAV_HEADER_ALLOWANCE = 1024
# Only the end of ffmpeg's error output is kept: a crafted file can make it
# print an error for every packet.
STDERR_TAIL_BYTES = 4096
# Exit statuses of the Linux wrapper, not of ffmpeg: the memory limit could not
# be set (125), or ffmpeg could not be started (126, 127).
SETUP_FAILURE_STATUSES = {125, 126, 127}


class AudioProcessingUnavailableError(Exception):
    """ffmpeg is missing, cannot start or is busy, so the file could not be checked."""


@dataclass(frozen=True)
class DecodedAudio:
    data: bytes  # mono 16-bit PCM WAV
    extension: str
    content_type: str
    duration_seconds: float


class AudioDecoder:
    """Runs ffmpeg in worker threads of its own, at most MAX_CONCURRENT_DECODES at once.

    One per application. Its limiters are created on first use, inside the
    event loop.
    """

    def __init__(self) -> None:
        self._slots: anyio.CapacityLimiter | None = None
        self._threads: anyio.CapacityLimiter | None = None

    async def decode(
        self, data: bytes, *, ffmpeg_path: str, sample_rate: int, max_seconds: float
    ) -> DecodedAudio | None:
        """Decode the upload to mono WAV, or return None if it is not supported audio."""
        if self._slots is None or self._threads is None:
            self._slots = anyio.CapacityLimiter(MAX_CONCURRENT_DECODES)
            self._threads = anyio.CapacityLimiter(MAX_CONCURRENT_DECODES)
        slots = self._slots
        try:
            with anyio.fail_after(DECODER_WAIT_SECONDS):
                await slots.acquire()
        except TimeoutError as exc:
            raise AudioProcessingUnavailableError("All audio decoders are busy.") from exc
        try:
            # Its own thread limiter, so decoding never uses the threads the
            # rest of the application shares.
            return await anyio.to_thread.run_sync(
                partial(
                    decode_blocking,
                    data,
                    ffmpeg_path=ffmpeg_path,
                    sample_rate=sample_rate,
                    max_seconds=max_seconds,
                ),
                limiter=self._threads,
            )
        finally:
            slots.release()


def decode_blocking(
    data: bytes, *, ffmpeg_path: str, sample_rate: int, max_seconds: float
) -> DecodedAudio | None:
    """Decode in the current thread; see AudioDecoder.decode."""
    executable = shutil.which(ffmpeg_path)
    if executable is None:
        raise AudioProcessingUnavailableError(f"ffmpeg not found at {ffmpeg_path!r}.")
    max_output_bytes = int((max_seconds + 1) * sample_rate) * OUTPUT_SAMPLE_BYTES
    with tempfile.TemporaryDirectory(prefix="voice-") as folder:
        source = Path(folder) / "upload"
        target = Path(folder) / "decoded.wav"
        source.write_bytes(data)
        command = _ffmpeg_command(
            executable, source, target, sample_rate, max_seconds, max_output_bytes
        )
        if not _run(command) or not target.exists():
            return None
        if target.stat().st_size > max_output_bytes + 2 * WAV_HEADER_ALLOWANCE:
            return None
        return _read_decoded(target.read_bytes())


def _ffmpeg_command(
    executable: str,
    source: Path,
    target: Path,
    sample_rate: int,
    max_seconds: float,
    max_output_bytes: int,
) -> list[str]:
    command = [
        executable, "-nostdin", "-hide_banner", "-loglevel", "error",
        "-max_alloc", str(MAX_ALLOCATION_BYTES), "-threads", "1",
        # Input options: what ffmpeg may open and decode.
        "-protocol_whitelist", "file",
        "-format_whitelist", ALLOWED_CONTAINERS,
        "-codec_whitelist", ALLOWED_DECODERS,
        "-max_streams", str(MAX_STREAMS),
        "-i", str(source),
        # Output: the first audio stream only, with no metadata, cut just past
        # the maximum so an overlong file is still reported as too long.
        "-map", "0:a:0", "-map_metadata", "-1", "-fflags", "+bitexact",
        "-t", str(max_seconds + 1),
        # -t follows timestamps, which a crafted file can repeat; this does not
        # (ffmpeg may overshoot it by one write, so the result is checked again).
        "-fs", str(max_output_bytes + WAV_HEADER_ALLOWANCE),
        "-ac", "1", "-ar", str(sample_rate), "-c:a", "pcm_s16le", "-f", "wav",
        str(target),
    ]  # fmt: skip
    if sys.platform.startswith("linux"):
        # Limit the whole process's memory: the shell sets the limit, then
        # becomes ffmpeg. Never above a limit the server already has, which the
        # shell could not raise.
        import resource  # Unix only

        soft, hard = resource.getrlimit(resource.RLIMIT_AS)
        limit_kib = str(memory_limit_bytes(soft, hard, resource.RLIM_INFINITY) // 1024)
        command = [
            "/bin/sh", "-c", 'ulimit -v "$0" || exit 125; exec "$@"', limit_kib, *command
        ]  # fmt: skip
    return command


def memory_limit_bytes(soft: int, hard: int, unlimited: int) -> int:
    """MAX_MEMORY_BYTES, or the server's own limit if that is lower."""
    inherited = [limit for limit in (soft, hard) if limit != unlimited]
    return min([MAX_MEMORY_BYTES, *inherited])


def _run(command: list[str]) -> bool:
    try:
        process = subprocess.Popen(
            command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
        )
    except OSError as exc:
        # Not executable, a directory, out of process handles, ...
        raise AudioProcessingUnavailableError(f"Could not start ffmpeg: {exc}") from exc
    assert process.stderr is not None
    tail: deque[bytes] = deque()
    reader = threading.Thread(target=keep_tail, args=(process.stderr, tail), daemon=True)
    # From here on, whatever happens, ffmpeg is stopped and reaped.
    try:
        try:
            reader.start()
        except RuntimeError as exc:  # no thread available
            raise AudioProcessingUnavailableError(f"Could not watch ffmpeg: {exc}") from exc
        status = process.wait(timeout=DECODE_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        logger.warning("ffmpeg took over %s seconds; rejecting the upload.", DECODE_TIMEOUT_SECONDS)
        return False
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        if reader.ident is not None:  # started
            reader.join(timeout=5)
        process.stderr.close()
    errors = b"".join(tail)[-STDERR_TAIL_BYTES:].decode(errors="replace")
    if status in SETUP_FAILURE_STATUSES and sys.platform.startswith("linux"):
        raise AudioProcessingUnavailableError(f"Could not start ffmpeg ({status}): {errors}")
    if status != 0:
        logger.info("ffmpeg rejected an upload: %s", errors)
        return False
    return True


def keep_tail(stream, tail: deque[bytes]) -> None:
    """Read the stream to the end, keeping only its last STDERR_TAIL_BYTES."""
    size = 0
    for chunk in iter(lambda: stream.read(4096), b""):
        tail.append(chunk)
        size += len(chunk)
        while size - len(tail[0]) >= STDERR_TAIL_BYTES:
            size -= len(tail.popleft())

def _read_decoded(output: bytes) -> DecodedAudio | None:
    try:
        with wave.open(io.BytesIO(output), "rb") as audio:
            frames = audio.getnframes()
            rate = audio.getframerate()
    except (wave.Error, EOFError):
        return None
    if frames == 0:
        return None
    return DecodedAudio(output, EXTENSION, CONTENT_TYPE, frames / rate)
