import io
import json
import struct
import wave
from datetime import UTC, datetime
from uuid import UUID

import httpx
import pytest

from app.core import clock
from app.integrations.supabase import SupabaseStorage, get_supabase_storage
from app.modules.voices.audio import inspect_audio
from supabase_mock import body, postgres_error, rows

ME = UUID(int=1)  # matches the test_user fixture
VOICE_ID = UUID(int=400)
NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
OLD_SAMPLE = f"{ME}/old.wav"
OLD_MODEL = f"{ME}/old.pt"
SAMPLES = "voice-samples"
MODELS = "voice-models"


def wav(seconds: float, rate: int = 8000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(b"\0\0" * int(rate * seconds))
    return buffer.getvalue()


def voice_row(**changes):
    return {
        "id": str(VOICE_ID),
        "status": "uploaded",
        "error_message": None,
        "sample_duration_seconds": 10.0,
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
        files={"audio": ("sample.wav", wav(10) if data is None else data, content_type)},
        data={"owner_consent": owner_consent},
    )


# Audio checks


def test_inspect_audio_reads_wav_length():
    info = inspect_audio(wav(7, rate=16000))
    assert (info.extension, info.content_type, info.duration_seconds) == ("wav", "audio/wav", 7.0)


@pytest.mark.parametrize(
    "data",
    [
        wav(10)[:44],  # header claims ten seconds, but no audio follows
        wav(10)[:-100],  # truncated
        b"",
        b"RIFF" + b"\0" * 100,
        b"ID3" + b"\0" * 1000,  # MP3 is not accepted
        # A chunk claiming more data than its parent holds (raises RuntimeError).
        b"RIFF" + struct.pack("<I", 12) + b"WAVEJUNK" + struct.pack("<I", 100),
    ],
)
def test_inspect_audio_rejects_incomplete_or_other_files(data):
    assert inspect_audio(data) is None


# PUT /users/me/voice


def test_first_upload(voice_client, db, storage):
    db.on("GET", "voices", rows())
    db.on("POST", "voices", rows(voice_row(), status=201))

    response = upload(voice_client)

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "uploaded"
    assert "sample_storage_path" not in data and "model_storage_path" not in data

    [stored] = storage.uploads
    new_path = storage.uploaded_path()
    assert new_path.startswith(f"{ME}/") and new_path.endswith(".wav")
    assert stored.headers["Content-Type"] == "audio/wav"
    assert stored.headers["x-upsert"] == "false"
    row = body(db.sent("POST", "voices")[0])
    assert row["user_id"] == str(ME)
    assert row["sample_storage_path"] == new_path
    assert row["sample_duration_seconds"] == pytest.approx(10.0)
    assert row["status"] == "uploaded"
    assert row["voice_owner_consent_at"] == "2026-11-10T12:00:00.000000Z"
    assert set(storage.objects[SAMPLES]) == {new_path}


def test_format_comes_from_contents_not_the_client(voice_client, db, storage):
    db.on("GET", "voices", rows())
    db.on("POST", "voices", rows(voice_row(), status=201))
    assert upload(voice_client, content_type="audio/mpeg").status_code == 200
    assert storage.uploads[0].headers["Content-Type"] == "audio/wav"


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
    assert values["status"] == "uploaded"
    assert values["model_storage_path"] is None
    assert values["error_message"] is None
    assert set(storage.objects[SAMPLES]) == {
        storage.uploaded_path(),
        f"{UUID(int=2)}/someone-else.wav",
    }
    assert storage.objects[MODELS] == {}


def test_replacing_keeps_files_of_a_newer_concurrent_upload(voice_client, db, storage):
    storage.put(SAMPLES, OLD_SAMPLE)
    db.on("GET", "voices", rows(voice_row(sample_storage_path=OLD_SAMPLE)))

    def patch_while_another_upload_lands(request):
        storage.put(SAMPLES, f"{ME}/newer.wav")
        return rows(voice_row())

    db.on("PATCH", "voices", patch_while_another_upload_lands)
    assert upload(voice_client).status_code == 200
    assert f"{ME}/newer.wav" in storage.objects[SAMPLES]
    assert OLD_SAMPLE not in storage.objects[SAMPLES]


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
        ({"data": wav(61)}, 400, "audio_too_long"),
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
        "sample_duration_seconds": 10.0,
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
    db.on("GET", "voices", rows(voice_row(sample_storage_path=OLD_SAMPLE)))
    db.on("DELETE", "voices", rows())  # the sample changed since it was read
    response = voice_client.delete("/api/v1/users/me/voice")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "voice_changed"
    assert OLD_SAMPLE in storage.objects[SAMPLES]


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


def test_failed_file_deletion_is_503_and_retry_finishes(voice_client, db, storage):
    storage.put(SAMPLES, OLD_SAMPLE)
    db.on("GET", "voices", rows(voice_row(sample_storage_path=OLD_SAMPLE)))
    db.on("DELETE", "voices", rows({"id": str(VOICE_ID)}))
    storage.fail_remove = httpx.Response(503)
    assert voice_client.delete("/api/v1/users/me/voice").status_code == 503

    storage.fail_remove = None
    db.on("GET", "voices", rows(), rows())
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
