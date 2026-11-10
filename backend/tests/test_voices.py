import io
import wave
from datetime import UTC, datetime
from uuid import UUID

import httpx
import pytest

from app.core import clock
from app.integrations.supabase import SupabaseStorage, get_supabase_storage
from supabase_mock import body, postgres_error, rows

ME = UUID(int=1)  # matches the test_user fixture
VOICE_ID = UUID(int=400)
NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
OLD_SAMPLE = f"{ME}/old.wav"
OLD_MODEL = f"{ME}/old.pt"


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
    """Records Storage calls; set `fail_upload` or `fail_remove` to a response to fail them."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.fail_upload: httpx.Response | None = None
        self.fail_remove: httpx.Response | None = None

    def __call__(self, request):
        self.requests.append(request)
        if request.method == "POST" and self.fail_upload is not None:
            return self.fail_upload
        if request.method == "DELETE" and self.fail_remove is not None:
            return self.fail_remove
        return httpx.Response(200, json={})

    @property
    def uploads(self):
        return [r for r in self.requests if r.method == "POST"]

    @property
    def removed(self):
        return [
            (r.url.path.removeprefix("/storage/v1/object/"), body(r)["prefixes"])
            for r in self.requests
            if r.method == "DELETE"
        ]


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
    bucket_path = stored.url.path.removeprefix("/storage/v1/object/")
    assert bucket_path.startswith(f"voice-samples/{ME}/") and bucket_path.endswith(".wav")
    assert stored.headers["Content-Type"] == "audio/wav"
    assert stored.headers["x-upsert"] == "false"
    row = body(db.sent("POST", "voices")[0])
    assert row["user_id"] == str(ME)
    assert row["sample_storage_path"] == bucket_path.removeprefix("voice-samples/")
    assert row["sample_duration_seconds"] == 10.0
    assert row["status"] == "uploaded"
    assert row["voice_owner_consent_at"] == "2026-11-10T12:00:00.000000Z"
    assert storage.removed == []


def test_format_comes_from_contents_not_the_client(voice_client, db, storage):
    db.on("GET", "voices", rows())
    db.on("POST", "voices", rows(voice_row(), status=201))
    assert upload(voice_client, content_type="audio/mpeg").status_code == 200
    assert storage.uploads[0].headers["Content-Type"] == "audio/wav"


def test_replacing_a_sample_deletes_the_old_files(voice_client, db, storage):
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
    assert storage.removed == [("voice-samples", [OLD_SAMPLE]), ("voice-models", [OLD_MODEL])]


def test_concurrent_replacement_is_409_and_cleans_up(voice_client, db, storage):
    db.on("GET", "voices", rows(voice_row(sample_storage_path=OLD_SAMPLE)))
    db.on("PATCH", "voices", rows())

    response = upload(voice_client)

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "voice_changed"
    new_path = storage.uploads[0].url.path.removeprefix("/storage/v1/object/voice-samples/")
    # The new file is removed; the old one is kept because the database still uses it.
    assert storage.removed == [("voice-samples", [new_path])]


def test_concurrent_first_upload_is_409(voice_client, db, storage):
    db.on("GET", "voices", rows())
    db.on("POST", "voices", postgres_error("23505", "voices_user_id_key"))
    response = upload(voice_client)
    assert response.json()["detail"]["code"] == "voice_changed"
    assert len(storage.removed) == 1


def test_upload_without_profile_is_409(voice_client, db, storage):
    db.on("GET", "voices", rows())
    db.on("POST", "voices", postgres_error("23503", "voices_user_id_fkey"))
    response = upload(voice_client)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "profile_required"
    assert len(storage.removed) == 1


def test_database_failure_after_upload_removes_the_file(voice_client, db, storage):
    db.on("GET", "voices", rows())
    db.on("POST", "voices", httpx.Response(503))
    response = upload(voice_client)
    assert response.status_code == 503
    new_path = storage.uploads[0].url.path.removeprefix("/storage/v1/object/voice-samples/")
    assert storage.removed == [("voice-samples", [new_path])]


def test_storage_failure_writes_nothing(voice_client, db, storage):
    db.on("GET", "voices", rows())
    storage.fail_upload = httpx.Response(503)
    response = upload(voice_client)
    assert response.status_code == 503
    assert db.sent("POST", "voices") == []


def test_old_files_that_fail_to_delete_do_not_fail_the_upload(voice_client, db, storage):
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


def test_delete_voice_removes_files_then_record(voice_client, db, storage):
    db.on("GET", "voices", rows(voice_row(sample_storage_path=OLD_SAMPLE, model_storage_path=OLD_MODEL)))
    db.on("DELETE", "voices", rows({"id": str(VOICE_ID)}))

    response = voice_client.delete("/api/v1/users/me/voice")

    assert response.status_code == 204
    assert storage.removed == [("voice-samples", [OLD_SAMPLE]), ("voice-models", [OLD_MODEL])]
    assert db.sent("DELETE", "voices")[0].url.params["user_id"] == f"eq.{ME}"


def test_delete_keeps_the_record_if_storage_fails(voice_client, db, storage):
    db.on("GET", "voices", rows(voice_row(sample_storage_path=OLD_SAMPLE)))
    storage.fail_remove = httpx.Response(503)
    response = voice_client.delete("/api/v1/users/me/voice")
    assert response.status_code == 503
    assert db.sent("DELETE", "voices") == []


def test_delete_missing_voice_is_404(voice_client, db):
    db.on("GET", "voices", rows())
    assert voice_client.delete("/api/v1/users/me/voice").status_code == 404


def test_storage_not_configured_is_503(authenticated_client):
    response = authenticated_client.delete("/api/v1/users/me/voice")
    assert response.status_code == 503
