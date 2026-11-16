"""Tests for the voice endpoints and audio decoding (app/modules/voices).

AI-assisted code (written with Claude Code), cited per the CSC398 AI policy.
"""

import array
import io
import json
import math
import os
import shutil
import struct
import subprocess
import sys
import wave
import zlib
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import anyio
import anyio.to_thread
import httpx
import pytest

from app.core import clock
from app.integrations.supabase import SupabaseStorage, get_supabase_storage
from app.modules.voices import audio as audio_module
from app.modules.voices.audio import (
    AudioDecoder,
    AudioProcessingUnavailableError,
    decode_blocking,
)
from supabase_mock import body, postgres_error, rows

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")

AUDIO = Path(__file__).parent / "fixtures" / "audio"  # seven-second recordings
ME = UUID(int=1)  # matches the test_user fixture
VOICE_ID = UUID(int=400)
NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
OLD_SAMPLE = f"{ME}/old.wav"
OLD_MODEL = f"{ME}/old.pt"
SAMPLES = "voice-samples"
MODELS = "voice-models"


def wav(
    seconds: float,
    rate: int = 8000,
    amplitude: float = 0.5,
    offset: int = 0,
    silence_before: float = 0,
) -> bytes:
    """A 440 Hz tone; amplitude is a fraction of full scale (0 for silence).

    `offset` adds a constant to every sample; `silence_before` seconds of
    silence come first and count towards `seconds`.
    """
    period = [
        offset + round(amplitude * 32767 * math.sin(2 * math.pi * 440 * n / rate))
        for n in range(rate)
    ]
    frames = int(rate * seconds)
    silent = int(rate * silence_before)
    tone = (period * ((frames - silent) // rate + 1))[: frames - silent]
    samples = array.array("h", [0] * silent + tone)
    if sys.byteorder == "big":
        samples.byteswap()
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(samples.tobytes())
    return buffer.getvalue()


def voice_row(**changes):
    return {
        "id": str(VOICE_ID),
        "status": "ready",
        "error_message": None,
        "sample_duration_seconds": 20.0,
        "voice_owner_consent_at": "2026-11-10T12:00:00+00:00",
        "updated_at": "2026-11-10T12:00:00+00:00",
        "sample_storage_path": f"{ME}/new.wav",
        "model_storage_path": None,
        **changes,
    }


class FakeStorage:
    """A stateful fake of the Storage endpoints the backend uses.

    Objects are stored per bucket as {path: created_at}; each upload is created
    one second after the last. Set `fail_upload`, `fail_remove` or `fail_list`
    to a response to make those calls fail.
    """

    def __init__(self):
        self.objects: dict[str, dict[str, str]] = {SAMPLES: {}, MODELS: {}}
        self.requests: list[httpx.Request] = []
        self.fail_upload: httpx.Response | None = None
        self.fail_remove: httpx.Response | None = None
        self.fail_list: httpx.Response | None = None
        self.folders = 1  # nested folders listed alongside the files (ignored)
        self._clock = 0

    def put(self, bucket, path, created_at=None):
        """Store an object; by default at 11:00:NN, an hour before the fixed clock."""
        self._clock += 1
        self.objects[bucket][path] = created_at or f"2026-11-10T11:00:{self._clock:02d}+00:00"

    def __call__(self, request):
        self.requests.append(request)
        path = request.url.path.removeprefix("/storage/v1/object/")
        if request.method == "POST" and path.startswith("list/"):
            if self.fail_list is not None:
                return self.fail_list
            bucket = path.removeprefix("list/")
            folder = body(request)["prefix"]
            listing = [
                {"id": name, "name": name.removeprefix(f"{folder}/"), "created_at": created}
                for name, created in self.objects[bucket].items()
                if name.startswith(f"{folder}/")
            ]
            listing.extend(
                {"id": None, "name": f"folder-{n}", "created_at": None}
                for n in range(self.folders)
            )
            start = body(request)["offset"]
            return httpx.Response(200, json=listing[start : start + body(request)["limit"]])
        if request.method == "POST":
            if self.fail_upload is not None:
                return self.fail_upload
            bucket, _, object_path = path.partition("/")
            self.put(bucket, object_path)
            return httpx.Response(200, json={"Key": path})
        if request.method == "DELETE":
            if self.fail_remove is not None:
                return self.fail_remove
            prefixes = json.loads(request.content)["prefixes"]
            if len(prefixes) > 1000:  # Supabase's limit per request
                return httpx.Response(400, json={"message": "Too many objects"})
            for name in prefixes:
                self.objects[path].pop(name, None)
            return httpx.Response(200, json=[])
        raise AssertionError(f"Unexpected Storage request: {request.method} {request.url}")

    @property
    def uploads(self):
        return [
            r for r in self.requests if r.method == "POST" and "/object/list/" not in r.url.path
        ]

    def uploaded_path(self):
        return self.uploads[0].url.path.removeprefix(f"/storage/v1/object/{SAMPLES}/")


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    monkeypatch.setattr(clock, "now", lambda: NOW)


@pytest.fixture
def storage():
    return FakeStorage()


@pytest.fixture
def voice_client(application, db_client, storage):
    async def override():
        transport = httpx.MockTransport(storage)
        async with httpx.AsyncClient(transport=transport) as http_client:
            yield SupabaseStorage("https://example.supabase.co", "secret", http_client)

    application.dependency_overrides[get_supabase_storage] = override
    yield db_client
    application.dependency_overrides.pop(get_supabase_storage, None)


def upload(client, data=None, owner_consent="true", content_type="audio/wav"):
    return client.put(
        "/api/v1/users/me/voice",
        files={"audio": ("sample.wav", wav(20) if data is None else data, content_type)},
        data={"owner_consent": owner_consent},
    )


# Audio checks


def decode(data, max_seconds=60.0):
    return decode_blocking(data, ffmpeg_path="ffmpeg", sample_rate=24000, max_seconds=max_seconds)


def read_wav(data):
    with wave.open(io.BytesIO(data), "rb") as audio:
        return audio.getnchannels(), audio.getsampwidth(), audio.getframerate()


@pytest.mark.parametrize(
    "name",
    ["voice.webm", "voice-fragmented.mp4", "voice.m4a", "voice.ogg", "voice.mp3"],
)
def test_decode_converts_browser_recordings_to_mono_wav(name):
    decoded = decode((AUDIO / name).read_bytes())
    assert decoded.duration_seconds == pytest.approx(7.0, abs=0.1)
    assert -30 < decoded.loudness_dbfs < -10
    assert (decoded.extension, decoded.content_type) == ("wav", "audio/wav")
    assert read_wav(decoded.data) == (1, 2, 24000)


def test_decode_converts_wav():
    decoded = decode(wav(7, rate=44100))
    assert decoded.duration_seconds == pytest.approx(7.0)
    assert read_wav(decoded.data) == (1, 2, 24000)


@pytest.mark.parametrize(
    ("amplitude", "expected"),
    [
        (0.5, -9.03),  # a sine's RMS is its amplitude / sqrt(2)
        (0.01, -43.0),
        (0.001, -63.0),
        (0, -math.inf),
    ],
)
def test_decode_measures_loudness(amplitude, expected):
    assert decode(wav(7, amplitude=amplitude)).loudness_dbfs == pytest.approx(expected, abs=0.5)


def test_loudness_ignores_a_constant_offset():
    # A constant signal (DC offset) is inaudible, whatever its value.
    assert decode(wav(7, amplitude=0, offset=1000)).loudness_dbfs == -math.inf
    tone = decode(wav(7, amplitude=0.01, offset=1000)).loudness_dbfs
    assert tone == pytest.approx(-43.0, abs=0.5)


def test_loudness_covers_only_the_start_chatterbox_uses():
    # Chatterbox conditions on at most the first 15 seconds.
    late = decode(wav(36, silence_before=16))
    assert late.loudness_dbfs == -math.inf
    pause = decode(wav(20, silence_before=3))  # a short pause before speaking
    assert pause.loudness_dbfs > -11


def test_decode_measures_the_audio_not_the_header():
    # The header claims ten seconds, but only two are there.
    decoded = decode(wav(10)[: 44 + 2 * 8000 * 2])
    assert decoded.duration_seconds == pytest.approx(2.0, abs=0.01)


def test_decode_stops_just_past_the_maximum():
    decoded = decode(wav(120), max_seconds=60)
    assert 60 < decoded.duration_seconds <= 61


def test_decode_drops_metadata():
    source = (AUDIO / "voice.mp3").read_bytes()
    assert b"Private title" in source
    assert b"Private title" not in decode(source).data


@pytest.mark.parametrize(
    "data",
    [
        wav(10)[:44],  # header only, no audio
        b"",
        b"definitely not audio" * 50,
        b"RIFF" + b"\0" * 100,
        b"ID3" + b"\0" * 1000,
        # A chunk claiming more data than its parent holds.
        b"RIFF" + struct.pack("<I", 12) + b"WAVEJUNK" + struct.pack("<I", 100),
    ],
)
def test_decode_rejects_files_without_audio(data):
    assert decode(data) is None


def encode(container, codec):
    """Seven seconds of tone, encoded by ffmpeg."""
    command = ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=d=7", "-c:a", codec, "-f", container, "-"]
    return subprocess.run(command, capture_output=True, check=True).stdout


@pytest.mark.parametrize(
    ("container", "codec"),
    [
        ("avi", "pcm_s16le"),  # an allowed decoder in a container browsers do not produce
        ("matroska", "pcm_s16be"),  # an allowed container with a decoder browsers do not use
    ],
)
def test_decode_accepts_only_browser_containers_and_codecs(container, codec):
    assert decode(encode(container, codec)) is None


def test_decode_does_not_follow_playlists():
    # An HLS playlist would make ffmpeg read other files (or URLs) on the server.
    # Recent ffmpeg also refuses it without an .m3u8 name; older versions do not.
    target = (AUDIO / "voice.mp3").resolve().as_uri()
    playlist = f"#EXTM3U\n#EXT-X-TARGETDURATION:10\n#EXTINF:7,\n{target}\n#EXT-X-ENDLIST\n"
    assert decode(playlist.encode()) is None


def test_decode_gives_up_on_slow_files(monkeypatch):
    monkeypatch.setattr(audio_module, "DECODE_TIMEOUT_SECONDS", 0.001)
    assert decode((AUDIO / "voice.webm").read_bytes()) is None


@pytest.mark.parametrize("ffmpeg_path", ["missing-ffmpeg-binary", str(AUDIO)])  # absent; a folder
def test_decode_without_a_runnable_ffmpeg_is_unavailable(ffmpeg_path):
    with pytest.raises(AudioProcessingUnavailableError):
        decode_blocking(wav(7), ffmpeg_path=ffmpeg_path, sample_rate=24000, max_seconds=60)


def test_only_the_end_of_ffmpeg_errors_is_kept():
    # A crafted file can make ffmpeg print an error per packet (100 MB seen).
    tail = deque()
    audio_module.keep_tail(io.BytesIO(b"x" * 1_000_000 + b"last line"), tail)
    kept = b"".join(tail)
    assert kept.endswith(b"last line")
    assert len(kept) < 2 * audio_module.STDERR_TAIL_BYTES


@pytest.mark.parametrize(
    ("soft", "hard", "expected"),
    [
        (-1, -1, audio_module.MAX_MEMORY_BYTES),  # no limit of its own
        (512 * 1024**2, -1, 512 * 1024**2),  # a lower limit cannot be raised
        (-1, 768 * 1024**2, 768 * 1024**2),
        (4 * 1024**3, 4 * 1024**3, audio_module.MAX_MEMORY_BYTES),
    ],
)
def test_memory_limit_never_exceeds_the_servers_own(soft, hard, expected):
    assert audio_module.memory_limit_bytes(soft, hard, -1) == expected


@pytest.mark.parametrize("status", [125, 126, 127])
def test_wrapper_setup_failures_are_unavailable_not_bad_audio(monkeypatch, status):
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(AudioProcessingUnavailableError):
        audio_module._run([sys.executable, "-c", f"raise SystemExit({status})"])


def test_ffmpeg_rejection_is_bad_audio(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert audio_module._run([sys.executable, "-c", "raise SystemExit(1)"]) is False

def test_ffmpeg_is_stopped_if_it_cannot_be_watched(monkeypatch):
    started = []
    popen = subprocess.Popen

    def record(*args, **kwargs):
        started.append(popen(*args, **kwargs))
        return started[-1]

    def no_threads(self):
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(audio_module.subprocess, "Popen", record)
    monkeypatch.setattr(audio_module.threading.Thread, "start", no_threads)
    with pytest.raises(AudioProcessingUnavailableError):
        audio_module._run([sys.executable, "-c", "import time; time.sleep(30)"])
    assert started[0].poll() is not None

# Crafted Matroska files (from the security review). Each builds an EBML element.
def ebml(element_id, payload):
    for size_bytes in range(1, 9):
        if len(payload) < (1 << (7 * size_bytes)) - 1:
            size = ((1 << (7 * size_bytes)) | len(payload)).to_bytes(size_bytes, "big")
            return bytes.fromhex(element_id) + size + payload
    raise ValueError("too large")


def ebml_uint(element_id, value):
    return ebml(element_id, value.to_bytes(max(1, (value.bit_length() + 7) // 8), "big"))


def matroska(tracks, blocks):
    header = ebml("1a45dfa3", ebml("4282", b"matroska") + ebml_uint("4287", 4) + ebml_uint("4285", 2))
    info = ebml("1549a966", ebml_uint("2ad7b1", 1_000_000))
    cluster = ebml("1f43b675", ebml_uint("e7", 0) + b"".join(blocks))
    return header + ebml("18538067", info + ebml("1654ae6b", b"".join(tracks)) + cluster)


def pcm_track(number, extra=b""):
    audio = ebml("e1", ebml("b5", struct.pack(">d", 24000)) + ebml_uint("9f", 1) + ebml_uint("6264", 16))
    return ebml("ae", ebml_uint("d7", number) + ebml_uint("73c5", number) + ebml_uint("83", 2)
                + ebml("86", b"A_PCM/INT/LIT") + extra + audio)  # fmt: skip


def test_decode_output_is_capped_even_if_timestamps_repeat(monkeypatch):
    # Header stripping expands each 4-byte block to a second of audio, every
    # block at time zero, so `-t` alone never stops the decode.
    written = []
    run = audio_module._run

    def run_and_measure(command):
        result = run(command)
        output = Path(command[-1])
        written.append(output.stat().st_size if output.exists() else 0)
        return result

    monkeypatch.setattr(audio_module, "_run", run_and_measure)
    stripped = ebml("6240", ebml_uint("5032", 1) + ebml("5034", ebml_uint("4254", 3) + ebml("4255", b"\0" * 48000)))
    track = pcm_track(1, ebml("6d80", stripped))
    data = matroska([track], [ebml("a3", b"\x81\x00\x00\x80\0\0")] * 1000)
    assert len(data) < 100_000
    decoded = decode(data)
    # ffmpeg itself stopped writing, about one write past the cap (61 s of
    # audio, 2.9 MB). Without the cap it writes 48 MB.
    assert written and written[0] < 4 * 1024 * 1024
    assert decoded is None or decoded.duration_seconds <= 61


def peak_ffmpeg_memory(data, folder):
    """Peak resident memory, in bytes, of ffmpeg run with the decoder's command."""
    source, target = folder / "upload", folder / "decoded.wav"
    source.write_bytes(data)
    command = audio_module._ffmpeg_command(
        shutil.which("ffmpeg"), source, target, 24000, 60, 3 * 1024 * 1024
    )
    process = subprocess.Popen(
        command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    _, wait_status, usage = os.wait4(process.pid, 0)
    process.returncode = os.waitstatus_to_exitcode(wait_status)
    # Linux reports KiB, macOS bytes.
    return usage.ru_maxrss * (1 if sys.platform == "darwin" else 1024)


def test_decode_ignores_streams_beyond_the_limit(tmp_path):
    # Each track's private data is a zlib bomb: parsing all 16 tracks takes over
    # 600 MiB. ffmpeg 4.x rejects the file, while 9.x decodes the first two
    # tracks (0.1 s, so the upload is still rejected as too short). Either way
    # the other tracks are never parsed, so memory stays low.
    bomb = ebml("63a2", zlib.compress(b"\0" * 6 * 1024 * 1024))
    compressed = ebml("6d80", ebml("6240", ebml_uint("5032", 2) + ebml("5034", ebml_uint("4254", 0))))
    tracks = [pcm_track(n, bomb + compressed) for n in range(1, 17)]
    data = matroska(tracks, [ebml("a3", b"\x81\x00\x00\x80" + b"\0" * 4800)])
    decoded = decode(data)
    assert decoded is None or decoded.duration_seconds < 1
    if hasattr(os, "wait4"):  # Linux and macOS; Windows cannot measure it this way
        assert peak_ffmpeg_memory(data, tmp_path) < 300 * 1024 * 1024


def run_async(function):
    return anyio.run(function)


async def decode_as(decoder, owner, data=None, ffmpeg_path="ffmpeg"):
    return await decoder.decode(
        wav(7) if data is None else data,
        owner=owner,
        ffmpeg_path=ffmpeg_path,
        sample_rate=24000,
        max_seconds=60,
    )


async def fill_decoders(decoder):
    """Take every decoder slot, as if other uploads were being decoded.

    Returns the tokens holding them, to release with release_on_behalf_of.
    """
    await decode_as(decoder, "warm-up")  # creates the limiters
    tokens = [object() for _ in range(audio_module.MAX_CONCURRENT_DECODES)]
    for token in tokens:
        await decoder._slots.acquire_on_behalf_of(token)
    return tokens


def test_decoder_gives_up_when_all_decoders_are_busy(monkeypatch):
    monkeypatch.setattr(audio_module, "DECODER_WAIT_SECONDS", 0.05)

    async def scenario():
        decoder = AudioDecoder()
        await fill_decoders(decoder)
        with pytest.raises(AudioProcessingUnavailableError):
            await decode_as(decoder, ME)

    run_async(scenario)


def test_waiting_uploads_do_not_hold_shared_threads(monkeypatch):
    monkeypatch.setattr(audio_module, "DECODER_WAIT_SECONDS", 0.2)

    async def scenario():
        decoder = AudioDecoder()
        await fill_decoders(decoder)
        shared = anyio.to_thread.current_default_thread_limiter()

        async def waiting_upload(owner):
            with pytest.raises(AudioProcessingUnavailableError):
                await decode_as(decoder, owner)

        async with anyio.create_task_group() as group:
            for owner in range(50):
                group.start_soon(waiting_upload, owner)
            await anyio.sleep(0.05)
            assert shared.borrowed_tokens == 0

    run_async(scenario)


def test_each_owner_has_one_upload_in_progress(monkeypatch):
    # Otherwise one account could hold every decoder while other users wait.
    monkeypatch.setattr(audio_module, "DECODER_WAIT_SECONDS", 0.2)

    async def scenario():
        decoder = AudioDecoder()
        tokens = await fill_decoders(decoder)
        outcomes = []

        async def waiting_upload(owner):
            try:
                await decode_as(decoder, owner)
            except (AudioProcessingUnavailableError, audio_module.DecodeInProgressError) as exc:
                outcomes.append((owner, type(exc).__name__))

        async with anyio.create_task_group() as group:
            group.start_soon(waiting_upload, "first")
            await anyio.sleep(0.05)  # "first" is now waiting for a decoder
            group.start_soon(waiting_upload, "first")
            group.start_soon(waiting_upload, "second")
        assert sorted(outcomes) == [
            ("first", "AudioProcessingUnavailableError"),
            ("first", "DecodeInProgressError"),
            ("second", "AudioProcessingUnavailableError"),
        ]
        # Every finished upload, failed or not, frees its owner.
        assert decoder._owners == set()
        for token in tokens:
            decoder._slots.release_on_behalf_of(token)
        assert (await decode_as(decoder, "first")).duration_seconds == pytest.approx(7.0)

    run_async(scenario)


def test_owner_is_freed_when_decoding_fails():
    async def scenario():
        decoder = AudioDecoder()
        assert await decode_as(decoder, ME, data=b"definitely not audio") is None
        with pytest.raises(AudioProcessingUnavailableError):
            await decode_as(decoder, ME, ffmpeg_path="missing-ffmpeg-binary")
        assert decoder._owners == set()
        assert await decode_as(decoder, ME) is not None

    run_async(scenario)


def test_owner_is_freed_when_the_upload_is_cancelled():
    async def scenario():
        decoder = AudioDecoder()
        await fill_decoders(decoder)
        with anyio.move_on_after(0.05):  # the client went away while it waited
            await decode_as(decoder, ME)
        assert decoder._owners == set()

    run_async(scenario)

# PUT /users/me/voice


def test_first_upload(voice_client, db, storage):
    db.on("GET", "voices", rows())
    db.on("POST", "voices", rows(voice_row(), status=201))

    response = upload(voice_client)

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ready"
    assert "sample_storage_path" not in data and "model_storage_path" not in data

    [stored] = storage.uploads
    new_path = storage.uploaded_path()
    assert new_path.startswith(f"{ME}/") and new_path.endswith(".wav")
    assert stored.headers["Content-Type"] == "audio/wav"
    assert stored.headers["x-upsert"] == "false"
    row = body(db.sent("POST", "voices")[0])
    assert row["user_id"] == str(ME)
    assert row["sample_storage_path"] == new_path
    assert row["sample_duration_seconds"] == pytest.approx(20.0)
    # No training step, so the voice can be used at once.
    assert row["status"] == "ready"
    assert row["voice_owner_consent_at"] == "2026-11-10T12:00:00.000000Z"
    assert set(storage.objects[SAMPLES]) == {new_path}


def test_format_comes_from_contents_not_the_client(voice_client, db, storage):
    db.on("GET", "voices", rows())
    db.on("POST", "voices", rows(voice_row(), status=201))
    assert upload(voice_client, content_type="audio/mpeg").status_code == 200
    assert storage.uploads[0].headers["Content-Type"] == "audio/wav"


def test_browser_recording_is_stored_as_wav(application, voice_client, db, storage):
    application.state.settings.voice_sample_min_seconds = 5  # the fixtures are 7 s
    db.on("GET", "voices", rows())
    db.on("POST", "voices", rows(voice_row(), status=201))
    response = upload(voice_client, (AUDIO / "voice.webm").read_bytes(), content_type="audio/webm")
    assert response.status_code == 200
    [stored] = storage.uploads
    assert stored.headers["Content-Type"] == "audio/wav"
    assert read_wav(stored.content) == (1, 2, 24000)
    assert body(db.sent("POST", "voices")[0])["sample_duration_seconds"] == pytest.approx(7.0, abs=0.1)


def test_upload_without_ffmpeg_is_503(application, voice_client, db, storage):
    application.state.settings.ffmpeg_path = "missing-ffmpeg-binary"
    response = upload(voice_client)
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "audio_processing_unavailable"
    assert storage.requests == []
    assert db.requests == []

def test_replacing_a_sample_deletes_older_files(voice_client, db, storage):
    storage.put(SAMPLES, OLD_SAMPLE)
    storage.put(MODELS, OLD_MODEL)
    storage.put(SAMPLES, f"{ME}/orphan-from-a-failed-upload.wav")
    storage.put(SAMPLES, f"{UUID(int=2)}/someone-else.wav")
    existing = voice_row(
        status="ready", sample_storage_path=OLD_SAMPLE, model_storage_path=OLD_MODEL
    )
    db.on("GET", "voices", rows(existing))
    db.on("PATCH", "voices", rows(voice_row()))

    response = upload(voice_client)

    assert response.status_code == 200
    request = db.sent("PATCH", "voices")[0]
    # Only replaces the sample this request read.
    assert request.url.params["sample_storage_path"] == f"eq.{OLD_SAMPLE}"
    assert request.url.params["user_id"] == f"eq.{ME}"
    values = body(request)
    assert values["status"] == "ready"
    assert values["model_storage_path"] is None
    assert values["error_message"] is None
    assert set(storage.objects[SAMPLES]) == {
        storage.uploaded_path(),
        f"{UUID(int=2)}/someone-else.wav",
    }
    assert storage.objects[MODELS] == {}


def test_replacing_deletes_the_replaced_files_even_if_recent(voice_client, db, storage):
    # Not left to the age-based sweep: the record says these files are replaced.
    recent = f"{ME}/recent.wav"
    storage.put(SAMPLES, recent, created_at="2026-11-10T11:59:30+00:00")
    storage.put(MODELS, f"{ME}/recent.pt", created_at="2026-11-10T11:59:40+00:00")
    existing = voice_row(sample_storage_path=recent, model_storage_path=f"{ME}/recent.pt")
    db.on("GET", "voices", rows(existing))
    db.on("PATCH", "voices", rows(voice_row()))
    assert upload(voice_client).status_code == 200
    assert set(storage.objects[SAMPLES]) == {storage.uploaded_path()}
    assert storage.objects[MODELS] == {}


def test_upload_keeps_recent_unreferenced_files(voice_client, db, storage):
    # Another upload stored this file but has not saved its record yet (it may
    # still succeed, e.g. after a delete), so it must survive this upload's sweep.
    storage.put(SAMPLES, f"{ME}/pending.wav", created_at="2026-11-10T11:59:00+00:00")
    storage.put(SAMPLES, f"{ME}/old-leftover.wav", created_at="2026-11-10T11:30:00+00:00")
    db.on("GET", "voices", rows())
    db.on("POST", "voices", rows(voice_row(), status=201))
    assert upload(voice_client).status_code == 200
    assert set(storage.objects[SAMPLES]) == {storage.uploaded_path(), f"{ME}/pending.wav"}


def test_concurrent_replacement_is_409_and_cleans_up(voice_client, db, storage):
    storage.put(SAMPLES, OLD_SAMPLE)
    db.on("GET", "voices", rows(voice_row(sample_storage_path=OLD_SAMPLE)))
    db.on("PATCH", "voices", rows())

    response = upload(voice_client)

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "voice_changed"
    # The new file is removed; the old one is kept because the database still uses it.
    assert set(storage.objects[SAMPLES]) == {OLD_SAMPLE}


def test_concurrent_first_upload_is_409(voice_client, db, storage):
    db.on("GET", "voices", rows())
    db.on("POST", "voices", postgres_error("23505", "voices_user_id_key"))
    response = upload(voice_client)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "voice_changed"
    assert storage.objects[SAMPLES] == {}


def test_upload_without_profile_is_409(voice_client, db, storage):
    db.on("GET", "voices", rows())
    db.on("POST", "voices", postgres_error("23503", "voices_user_id_fkey"))
    response = upload(voice_client)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "profile_required"
    assert storage.objects[SAMPLES] == {}


def test_unknown_database_outcome_keeps_the_new_file(voice_client, db, storage):
    # The write may have been saved, so deleting the file could break the voice.
    # A later upload or delete sweeps it if it is unused.
    db.on("GET", "voices", rows())
    db.on("POST", "voices", httpx.Response(503))
    response = upload(voice_client)
    assert response.status_code == 503
    assert set(storage.objects[SAMPLES]) == {storage.uploaded_path()}


BUCKET_NOT_FOUND = httpx.Response(404, json={"message": "Bucket not found"})


def test_missing_bucket_on_upload_is_503(voice_client, db, storage, caplog):
    # For example, migration 0004 was never applied.
    db.on("GET", "voices", rows())
    storage.fail_upload = BUCKET_NOT_FOUND
    response = upload(voice_client)
    assert response.status_code == 503
    assert response.json() == {"detail": "A backend service is unavailable. Try again later."}
    assert db.sent("POST", "voices") == []
    assert "Bucket not found" in caplog.text


def test_missing_bucket_on_delete_is_503(voice_client, db, storage):
    db.on("GET", "voices", rows())  # no voice yet
    db.on("GET", "voices", rows())  # still none before the sweep
    storage.fail_list = BUCKET_NOT_FOUND
    response = voice_client.delete("/api/v1/users/me/voice")
    assert response.status_code == 503


def test_storage_failure_writes_nothing(voice_client, db, storage):
    db.on("GET", "voices", rows())
    storage.fail_upload = httpx.Response(503)
    response = upload(voice_client)
    assert response.status_code == 503
    assert db.sent("POST", "voices") == []


def test_cleanup_failure_does_not_fail_the_upload(voice_client, db, storage):
    storage.put(SAMPLES, OLD_SAMPLE)
    db.on("GET", "voices", rows(voice_row(sample_storage_path=OLD_SAMPLE)))
    db.on("PATCH", "voices", rows(voice_row()))
    storage.fail_remove = httpx.Response(503)
    assert upload(voice_client).status_code == 200


@pytest.mark.parametrize(
    ("kwargs", "status", "code"),
    [
        ({"owner_consent": "false"}, 400, "owner_consent_required"),
        ({"data": wav(2)}, 400, "audio_too_short"),
        # Chatterbox Turbo needs over 5 s; 15 s covers both Chatterbox variants.
        ({"data": wav(14.9)}, 400, "audio_too_short"),
        ({"data": wav(61)}, 400, "audio_too_long"),
        ({"data": wav(20, amplitude=0)}, 400, "audio_too_quiet"),
        ({"data": wav(20, amplitude=0.001)}, 400, "audio_too_quiet"),  # about -63 dBFS
        ({"data": wav(20, amplitude=0, offset=1000)}, 400, "audio_too_quiet"),
        ({"data": wav(36, silence_before=16)}, 400, "audio_too_quiet"),
        ({"data": b"definitely not audio" * 50}, 415, "unsupported_audio"),
        ({"data": wav(10)[:44]}, 415, "unsupported_audio"),
        ({"data": b""}, 415, "unsupported_audio"),
    ],
)
def test_invalid_uploads_store_nothing(voice_client, db, storage, kwargs, status, code):
    response = upload(voice_client, **kwargs)
    assert response.status_code == status
    assert response.json()["detail"]["code"] == code
    assert storage.requests == []
    assert db.requests == []


@pytest.mark.parametrize(("seconds", "amplitude"), [(15, 0.5), (20, 0.01)])  # 15 s; -43 dBFS
def test_uploads_at_the_limits_are_accepted(voice_client, db, storage, seconds, amplitude):
    db.on("GET", "voices", rows())
    db.on("POST", "voices", rows(voice_row(), status=201))
    assert upload(voice_client, wav(seconds, amplitude=amplitude)).status_code == 200


def test_second_upload_while_the_first_is_decoding_is_429(application, voice_client, db, storage):
    application.state.audio_decoder._owners.add(ME)  # an upload of ME's is decoding
    response = upload(voice_client)
    assert response.status_code == 429
    assert response.json()["detail"]["code"] == "upload_in_progress"
    assert storage.requests == []
    assert db.requests == []


def test_too_large_upload_is_413(application, voice_client, db, storage):
    application.state.settings.voice_sample_max_bytes = 1000
    response = upload(voice_client)
    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "file_too_large"
    assert storage.requests == []


def test_upload_requires_owner_consent_field(voice_client, db, storage):
    response = voice_client.put(
        "/api/v1/users/me/voice", files={"audio": ("sample.wav", wav(10), "audio/wav")}
    )
    assert response.status_code == 422
    assert storage.requests == []


# GET and DELETE /users/me/voice


def test_get_voice(voice_client, db):
    db.on("GET", "voices", rows(voice_row(status="failed", error_message="Too noisy")))
    response = voice_client.get("/api/v1/users/me/voice")
    assert response.status_code == 200
    assert response.json() == {
        "status": "failed",
        "error_message": "Too noisy",
        "sample_duration_seconds": 20.0,
        "owner_consent_at": "2026-11-10T12:00:00Z",
        "updated_at": "2026-11-10T12:00:00Z",
    }
    assert db.sent("GET", "voices")[0].url.params["user_id"] == f"eq.{ME}"


def test_get_missing_voice_is_404(voice_client, db):
    db.on("GET", "voices", rows())
    response = voice_client.get("/api/v1/users/me/voice")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "voice_not_found"


def test_delete_voice_removes_record_and_all_files(voice_client, db, storage):
    storage.put(SAMPLES, OLD_SAMPLE)
    storage.put(SAMPLES, f"{ME}/orphan.wav")
    storage.put(MODELS, OLD_MODEL)
    storage.put(SAMPLES, f"{UUID(int=2)}/someone-else.wav")
    db.on("GET", "voices", rows(voice_row(sample_storage_path=OLD_SAMPLE)), rows())
    db.on("DELETE", "voices", rows({"id": str(VOICE_ID)}))

    response = voice_client.delete("/api/v1/users/me/voice")

    assert response.status_code == 204
    params = db.sent("DELETE", "voices")[0].url.params
    assert params["user_id"] == f"eq.{ME}"
    assert params["sample_storage_path"] == f"eq.{OLD_SAMPLE}"
    assert set(storage.objects[SAMPLES]) == {f"{UUID(int=2)}/someone-else.wav"}
    assert storage.objects[MODELS] == {}


def test_delete_during_a_replacement_is_409(voice_client, db, storage):
    storage.put(SAMPLES, OLD_SAMPLE)
    storage.put(SAMPLES, f"{ME}/replacement.wav", created_at="2026-11-10T11:59:59+00:00")
    db.on("GET", "voices", rows(voice_row(sample_storage_path=OLD_SAMPLE)))
    db.on("DELETE", "voices", rows())  # an upload replaced the sample since it was read
    response = voice_client.delete("/api/v1/users/me/voice")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "voice_changed"
    # The replaced sample is no longer referenced; the replacement is kept.
    assert set(storage.objects[SAMPLES]) == {f"{ME}/replacement.wav"}


def test_delete_keeps_files_of_a_voice_uploaded_meanwhile(voice_client, db, storage):
    storage.put(SAMPLES, OLD_SAMPLE)
    storage.put(SAMPLES, f"{ME}/newer.wav")
    db.on(
        "GET",
        "voices",
        rows(voice_row(sample_storage_path=OLD_SAMPLE)),
        rows(voice_row(sample_storage_path=f"{ME}/newer.wav")),
    )
    db.on("DELETE", "voices", rows({"id": str(VOICE_ID)}))
    assert voice_client.delete("/api/v1/users/me/voice").status_code == 204
    assert set(storage.objects[SAMPLES]) == {f"{ME}/newer.wav"}


def test_delete_without_voice_still_removes_leftover_files(voice_client, db, storage):
    storage.put(SAMPLES, f"{ME}/orphan.wav")
    db.on("GET", "voices", rows(), rows())
    assert voice_client.delete("/api/v1/users/me/voice").status_code == 204
    assert storage.objects[SAMPLES] == {}


def test_failed_file_deletion_keeps_the_record_so_retry_finishes(voice_client, db, storage):
    # Recent, so only the record (not the leftover sweep) can lead a retry to it.
    recent = f"{ME}/recent.wav"
    storage.put(SAMPLES, recent, created_at="2026-11-10T11:59:30+00:00")
    db.on("GET", "voices", rows(voice_row(sample_storage_path=recent)))
    storage.fail_remove = httpx.Response(503)
    assert voice_client.delete("/api/v1/users/me/voice").status_code == 503
    assert db.sent("DELETE", "voices") == []

    storage.fail_remove = None
    db.on("GET", "voices", rows(voice_row(sample_storage_path=recent)), rows())
    db.on("DELETE", "voices", rows({"id": str(VOICE_ID)}))
    assert voice_client.delete("/api/v1/users/me/voice").status_code == 204
    assert storage.objects[SAMPLES] == {}


def test_delete_keeps_recent_unreferenced_files(voice_client, db, storage):
    # A concurrent upload may have stored its file but not yet saved its record.
    storage.put(SAMPLES, OLD_SAMPLE, created_at="2026-11-10T11:58:00+00:00")
    storage.put(SAMPLES, f"{ME}/in-flight.wav", created_at="2026-11-10T11:59:00+00:00")
    storage.put(SAMPLES, f"{ME}/old-leftover.wav", created_at="2026-11-10T11:30:00+00:00")
    db.on("GET", "voices", rows(voice_row(sample_storage_path=OLD_SAMPLE)), rows())
    db.on("DELETE", "voices", rows({"id": str(VOICE_ID)}))

    assert voice_client.delete("/api/v1/users/me/voice").status_code == 204

    # The deleted voice's own file goes at once, even though it is recent.
    assert set(storage.objects[SAMPLES]) == {f"{ME}/in-flight.wav"}


def test_cleanup_removes_more_than_one_batch(voice_client, db, storage):
    for n in range(1205):
        storage.put(SAMPLES, f"{ME}/leftover-{n}.wav", created_at="2026-11-10T10:00:00+00:00")
    storage.folders = 150  # more entries than a page, none of them files
    db.on("GET", "voices", rows(), rows())
    assert voice_client.delete("/api/v1/users/me/voice").status_code == 204
    assert storage.objects[SAMPLES] == {}


def test_storage_not_configured_is_503(authenticated_client):
    response = authenticated_client.delete("/api/v1/users/me/voice")
    assert response.status_code == 503


# Request size limit


def test_oversized_request_is_rejected_before_authentication(client):
    response = client.put(
        "/api/v1/users/me/voice",
        files={"audio": ("sample.wav", b"\0" * (11 * 1024 * 1024), "audio/wav")},
        data={"owner_consent": "true"},
    )
    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "request_too_large"


def test_oversized_chunked_request_is_rejected(client):
    def chunks():
        for _ in range(200):
            yield b"\0" * 65536

    response = client.post(
        "/api/v1/friend-requests",
        content=chunks(),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 413
