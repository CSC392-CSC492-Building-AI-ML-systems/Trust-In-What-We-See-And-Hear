"""Decode an uploaded voice sample with ffmpeg and convert it to one stored format.

Browsers record WebM/Opus (Chrome, Firefox) or MP4/AAC (Safari), so those are
accepted along with Ogg, MP3, FLAC and WAV. A compressed file's header can
claim any length, so the only trustworthy length is the audio ffmpeg actually
decodes. Every sample is stored as mono 16-bit PCM WAV at one sample rate, so
the voice pipeline only ever reads one format.

The decoded audio is then tidied (`tidy`) before it is checked and stored,
because the voice engines clone from all of it: silence at the start and end
is cut, long pauses are shortened, the result is capped at
`MAX_REFERENCE_SECONDS` and leveled to `TARGET_DBFS`. A loud, clipped
reference with long pauses made Ming run on and garble its output.

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
- a time limit, and at most `MAX_CONCURRENT_DECODES` at once, and one per
  user. Uploads wait for a free decoder without holding a worker thread.

The client's file name and content type are ignored. The recorded length, the
length of the tidied audio and its loudness are reported, so the upload can
reject recordings that are too long, too short or silent.

AI-assisted code (written with Claude Code), cited per the CSC398 AI policy.
"""

import array
import io
import logging
import math
import shutil
import subprocess
import sys
import tempfile
import threading
import wave
from collections import deque
from collections.abc import Hashable, Sequence
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
# Audio, plus video if the recording has it. ffmpeg 4.x rejects a file with
# more streams; recent versions (9.x) ignore the extra streams instead. Either
# way they are never parsed, which is what bounds memory.
MAX_STREAMS = 2
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
# Tidying (see `tidy`). Levels are measured in 50 ms frames. A frame is
# silent when it is this far below the loudest speech (the 95th percentile
# frame, so a click does not count), and always below the floor.
FRAME_SECONDS = 0.05
SILENCE_BELOW_SPEECH_DB = 35
SILENCE_FLOOR_DBFS = -70
# Kept before the first and after the last speech, so soft word edges stay.
EDGE_SECONDS = 0.2
# A pause longer than LONG_PAUSE_SECONDS is shortened to KEPT_PAUSE_SECONDS.
LONG_PAUSE_SECONDS = 1.0
KEPT_PAUSE_SECONDS = 0.5
# The voice engines clone from the whole reference; more than this adds little
# and slows them down.
MAX_REFERENCE_SECONDS = 30
# A cut at the cap goes in the last pause after this, if there is one.
EARLIEST_CUT_SECONDS = 20
# Speech level the reference is leveled to (RMS), and the peak it may not
# pass (-1 dBFS), so leveling never clips.
TARGET_DBFS = -20
PEAK_LIMIT = 0.89
END_FADE_SECONDS = 0.01
WAV_HEADER_ALLOWANCE = 1024
# Only the end of ffmpeg's error output is kept: a crafted file can make it
# print an error for every packet.
STDERR_TAIL_BYTES = 4096
# Exit statuses of the Linux wrapper, not of ffmpeg: the memory limit could not
# be set (125), or ffmpeg could not be started (126, 127).
SETUP_FAILURE_STATUSES = {125, 126, 127}


class AudioProcessingUnavailableError(Exception):
    """ffmpeg is missing, cannot start or is busy, so the file could not be checked."""


class DecodeInProgressError(Exception):
    """The same owner already has an upload being decoded."""


@dataclass(frozen=True)
class DecodedAudio:
    data: bytes  # the tidied audio, mono 16-bit PCM WAV
    extension: str
    content_type: str
    # Length of the tidied audio, which is what gets stored.
    duration_seconds: float
    # Length of the audio as decoded, before tidying.
    recorded_seconds: float
    # Average (RMS) level of the tidied audio before leveling, relative to full
    # scale; -inf if nothing in it is louder than silence.
    loudness_dbfs: float


class AudioDecoder:
    """Runs ffmpeg in worker threads of its own, at most MAX_CONCURRENT_DECODES at once.

    Each owner (user) has at most one upload waiting or decoding, so one
    account cannot hold every decoder while other users wait. One per
    application, and per server process like the decoders themselves. Its
    limiters are created on first use, inside the event loop.
    """

    def __init__(self) -> None:
        self._slots: anyio.CapacityLimiter | None = None
        self._threads: anyio.CapacityLimiter | None = None
        self._owners: set[Hashable] = set()

    async def decode(
        self,
        data: bytes,
        *,
        owner: Hashable,
        ffmpeg_path: str,
        sample_rate: int,
        max_seconds: float,
    ) -> DecodedAudio | None:
        """Decode the upload to mono WAV, or return None if it is not supported audio.

        Raises DecodeInProgressError if `owner` already has an upload waiting
        or decoding.
        """
        # No await between the check and the add, so two uploads from one
        # owner cannot both get past it.
        if owner in self._owners:
            raise DecodeInProgressError("This owner already has an upload being decoded.")
        self._owners.add(owner)
        try:
            return await self._decode(
                data, ffmpeg_path=ffmpeg_path, sample_rate=sample_rate, max_seconds=max_seconds
            )
        finally:
            self._owners.discard(owner)

    async def _decode(
        self, data: bytes, *, ffmpeg_path: str, sample_rate: int, max_seconds: float
    ) -> DecodedAudio | None:
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
            pcm = audio.readframes(frames)
    except (wave.Error, EOFError):
        return None
    if frames == 0:
        return None
    samples = _samples(pcm)
    tidied, loudness = tidy(samples, rate)
    return DecodedAudio(
        _wav(tidied, rate), EXTENSION, CONTENT_TYPE, len(tidied) / rate, frames / rate, loudness
    )


def tidy(samples: Sequence[int], rate: int) -> tuple[array.array, float]:
    """Make a reference the voice engines clone well from.

    Cuts silence at the start and end (keeping EDGE_SECONDS), shortens pauses
    longer than LONG_PAUSE_SECONDS to KEPT_PAUSE_SECONDS, caps the result at
    MAX_REFERENCE_SECONDS (in a pause if there is one late enough), removes any
    DC offset and levels it to TARGET_DBFS without peaks above PEAK_LIMIT.

    Returns the samples and their loudness before leveling. Audio with nothing
    louder than silence gives no samples and -inf. Clipping in the recording
    cannot be undone; leveling only makes a loud one quieter.
    """
    frame = max(1, round(FRAME_SECONDS * rate))
    levels = [_level(samples[i : i + frame]) for i in range(0, len(samples), frame)]
    heard = sorted(level for level in levels if level > -math.inf)
    if not heard:
        return array.array("h"), -math.inf
    loudest = heard[min(len(heard) - 1, int(0.95 * len(heard)))]
    threshold = max(loudest - SILENCE_BELOW_SPEECH_DB, SILENCE_FLOOR_DBFS)
    speech = [level >= threshold for level in levels]
    if not any(speech):
        return array.array("h"), -math.inf

    first = speech.index(True)
    last = len(speech) - 1 - speech[::-1].index(True)
    edge = round(EDGE_SECONDS * rate)
    half_pause = round(KEPT_PAUSE_SECONDS * rate / 2)
    long_pause = math.ceil(LONG_PAUSE_SECONDS / FRAME_SECONDS)
    kept: list[tuple[int, int]] = []
    start = max(0, first * frame - edge)
    index = first
    while index <= last:
        if speech[index]:
            index += 1
            continue
        run_end = index
        while not speech[run_end]:
            run_end += 1
        if run_end - index > long_pause:
            kept.append((start, index * frame + half_pause))
            start = run_end * frame - half_pause
        index = run_end
    kept.append((start, min(len(samples), (last + 1) * frame + edge)))

    out = array.array("h")
    for begin, end in kept:
        out.extend(samples[begin:end])
    out = _cap(out, rate, frame, threshold)
    if not out:
        return out, -math.inf
    mean = sum(out) / len(out)
    loudness = _level(out)
    peak = max(abs(value - mean) for value in out)
    gain = 10 ** ((TARGET_DBFS - loudness) / 20)
    if peak * gain > PEAK_LIMIT * 32767:
        gain = PEAK_LIMIT * 32767 / peak
    fade = min(len(out), round(END_FADE_SECONDS * rate))
    leveled = array.array("h", (round((value - mean) * gain) for value in out))
    for i in range(fade):
        leveled[len(leveled) - 1 - i] = round(leveled[len(leveled) - 1 - i] * i / fade)
    return leveled, loudness


def _cap(samples: array.array, rate: int, frame: int, threshold: float) -> array.array:
    """At most MAX_REFERENCE_SECONDS, cut in the last pause after EARLIEST_CUT_SECONDS.

    The cut keeps EDGE_SECONDS after the speech before that pause.
    """
    limit = round(MAX_REFERENCE_SECONDS * rate)
    if len(samples) <= limit:
        return samples
    earliest = round(EARLIEST_CUT_SECONDS * rate)

    def silent(start: int) -> bool:
        return _level(samples[start : start + frame]) < threshold

    for start in range(limit - frame, earliest - 1, -frame):
        if silent(start):
            end = start + frame
            while start - frame >= earliest and silent(start - frame):
                start -= frame
            return samples[: min(end, start + round(EDGE_SECONDS * rate))]
    return samples[:limit]


def _samples(pcm: bytes) -> array.array:
    """16-bit little-endian mono PCM as numbers."""
    samples = array.array("h", pcm[: len(pcm) // 2 * 2])
    if sys.byteorder == "big":
        samples.byteswap()
    return samples


def _wav(samples: array.array, rate: int) -> bytes:
    data = array.array("h", samples)
    if sys.byteorder == "big":
        data.byteswap()
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(OUTPUT_SAMPLE_BYTES)
        audio.setframerate(rate)
        audio.writeframes(data.tobytes())
    return buffer.getvalue()


def loudness_dbfs(pcm: bytes) -> float:
    """RMS level of 16-bit little-endian mono PCM, in dB relative to full scale.

    The mean (DC offset) is removed first: a constant signal is inaudible.
    """
    return _level(_samples(pcm))


def _level(samples: Sequence[int]) -> float:
    """RMS level in dBFS with the mean removed; -inf for silence or no samples."""
    if not samples:
        return -math.inf
    count = len(samples)
    mean = sum(samples) / count
    variance = math.sumprod(samples, samples) / count - mean * mean
    if variance <= 0:
        return -math.inf
    return 10 * math.log10(variance / 32768**2)
