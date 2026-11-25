"""Tests for identity verification with Didit.

AI-assisted code (written with Claude Code), cited per the CSC398 AI policy.
"""

import json
from uuid import UUID

import httpx
import pytest
from pydantic import SecretStr

from app.integrations.identity_verification import (
    DiditClient,
    get_didit_client,
    normalize_status,
    optional_didit_client,
)
from supabase_mock import body, postgres_error, rows

ME = UUID(int=1)  # matches the test_user fixture
VERIFICATION_ID = UUID(int=500)
OTHER_VERIFICATION_ID = UUID(int=501)
SESSION_ID = "11111111-2222-3333-4444-555555555555"
SESSION_URL = "https://verify.didit.me/session/abc"
WORKFLOW_ID = "workflow-1"
API_KEY = "didit-key"
RETURN_URL = "http://localhost:3000/verify-identity"
URL = "/api/v1/users/me/verification"


class FakeDidit:
    """Didit's API: queued replies, used once each, in order."""

    def __init__(self) -> None:
        self.replies: list[httpx.Response | Exception] = []
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self.replies:
            raise AssertionError(f"Unexpected Didit request: {request.method} {request.url}")
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


@pytest.fixture
def didit(application):
    fake = FakeDidit()
    settings = application.state.settings
    settings.didit_api_key = SecretStr(API_KEY)
    settings.didit_workflow_id = WORKFLOW_ID
    settings.identity_verification_return_url = RETURN_URL

    async def override():
        async with httpx.AsyncClient(transport=httpx.MockTransport(fake)) as http_client:
            yield DiditClient("https://didit.example", API_KEY, http_client)

    application.dependency_overrides[get_didit_client] = override
    application.dependency_overrides[optional_didit_client] = override
    yield fake
    application.dependency_overrides.pop(get_didit_client, None)
    application.dependency_overrides.pop(optional_didit_client, None)
    assert not fake.replies, "Queued Didit replies were never requested"


def profile(verified=False):
    return {"id": str(ME), "verified_at": "2026-11-20T00:00:00+00:00" if verified else None}


def verification(status="not_started", id=VERIFICATION_ID):
    return {
        "id": str(id),
        "user_id": str(ME),
        "provider_session_id": SESSION_ID,
        "status": status,
        "created_at": "2026-11-23T11:00:00+00:00",
        "updated_at": "2026-11-23T11:00:00+00:00",
    }


def session(status="Not Started"):
    return httpx.Response(
        201,
        json={
            "session_id": SESSION_ID,
            "session_token": "client-token",
            "url": SESSION_URL,
            "status": status,
            "workflow_id": WORKFLOW_ID,
            "vendor_data": str(ME),
        },
    )


def decision(status, vendor_data=str(ME)):
    return httpx.Response(
        200,
        json={
            "session_id": SESSION_ID,
            "status": status,
            "vendor_data": vendor_data,
            # Personal data the backend must ignore.
            "id_verifications": [{"first_name": "Alex", "document_number": "X123"}],
        },
    )


# ---------------------------------------------------------------- POST /users/me/verification


def test_start_creates_a_session_and_returns_its_page(db_client, db, didit):
    db.on("GET", "users", rows(profile()))
    db.on("GET", "identity_verifications", rows())
    db.on("POST", "identity_verifications", rows(verification(), status=201))
    didit.replies.append(session())

    response = db_client.post(URL)

    assert response.status_code == 202
    assert response.json() == {
        "verification_id": str(VERIFICATION_ID),
        "status": "not_started",
        "url": SESSION_URL,
    }
    [request] = didit.requests
    assert request.method == "POST"
    assert request.url == "https://didit.example/v3/session/"
    assert request.headers["x-api-key"] == API_KEY
    assert json.loads(request.content) == {
        "workflow_id": WORKFLOW_ID,
        "vendor_data": str(ME),
        "callback": RETURN_URL,
    }
    assert body(db.sent("POST", "identity_verifications")[0]) == {
        "user_id": str(ME),
        "provider_session_id": SESSION_ID,
        "status": "not_started",
    }


def test_start_without_a_return_url_sends_no_callback(application, db_client, db, didit):
    application.state.settings.identity_verification_return_url = None
    db.on("GET", "users", rows(profile()))
    db.on("GET", "identity_verifications", rows())
    db.on("POST", "identity_verifications", rows(verification(), status=201))
    didit.replies.append(session())

    assert db_client.post(URL).status_code == 202
    assert "callback" not in json.loads(didit.requests[0].content)


def test_start_again_returns_the_unfinished_session(db_client, db, didit):
    # Didit hands back the same session, which is already stored.
    db.on("GET", "users", rows(profile()))
    db.on(
        "GET",
        "identity_verifications",
        rows(verification()),  # the latest session
        rows(verification()),  # the stored row for the returned session
    )
    db.on(
        "POST",
        "identity_verifications",
        postgres_error("23505", "identity_verifications_provider_session_id_key"),
    )
    db.on("PATCH", "identity_verifications", rows(verification("in_progress")))
    didit.replies += [decision("Not Started"), session("In Progress")]

    response = db_client.post(URL)

    assert response.status_code == 202
    assert response.json()["status"] == "in_progress"
    assert response.json()["verification_id"] == str(VERIFICATION_ID)
    lookup = db.sent("GET", "identity_verifications")[1]
    assert lookup.url.params["provider_session_id"] == f"eq.{SESSION_ID}"
    assert lookup.url.params["user_id"] == f"eq.{ME}"


def test_start_finds_the_unfinished_session_was_approved(db_client, db, didit):
    # The user passed on Didit, but no GET has recorded it yet.
    db.on("GET", "users", rows(profile()))
    db.on("GET", "identity_verifications", rows(verification("in_progress")))
    db.on("PATCH", "users", rows({"id": str(ME)}))
    db.on("PATCH", "identity_verifications", rows(verification("approved")))
    didit.replies.append(decision("Approved"))

    response = db_client.post(URL)

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "already_verified"
    [request] = didit.requests  # no new session
    assert request.method == "GET"


def test_start_finds_the_unfinished_session_is_in_review(db_client, db, didit):
    db.on("GET", "users", rows(profile()))
    db.on("GET", "identity_verifications", rows(verification("in_progress")))
    db.on("PATCH", "identity_verifications", rows(verification("in_review")))
    didit.replies.append(decision("In Review"))

    response = db_client.post(URL)

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "verification_in_review"
    assert len(didit.requests) == 1


def test_start_when_the_unfinished_session_cannot_be_checked(db_client, db, didit):
    db.on("GET", "users", rows(profile()))
    db.on("GET", "identity_verifications", rows(verification("in_progress")))
    didit.replies.append(httpx.Response(503))

    response = db_client.post(URL)

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "verification_unavailable"
    assert len(didit.requests) == 1


def test_start_when_didit_returns_another_users_session(db_client, db, didit):
    db.on("GET", "users", rows(profile()))
    db.on("GET", "identity_verifications", rows(), rows())  # latest; lookup by session and user
    db.on(
        "POST",
        "identity_verifications",
        postgres_error("23505", "identity_verifications_provider_session_id_key"),
    )
    didit.replies.append(session())

    response = db_client.post(URL)

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "verification_unavailable"


def test_start_when_already_verified(db_client, db, didit):
    db.on("GET", "users", rows(profile(verified=True)))

    response = db_client.post(URL)

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "already_verified"
    assert not didit.requests


def test_start_while_in_review(db_client, db, didit):
    db.on("GET", "users", rows(profile()))
    db.on("GET", "identity_verifications", rows(verification("in_review")))
    didit.replies.append(decision("In Review"))  # still being reviewed

    response = db_client.post(URL)

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "verification_in_review"
    [request] = didit.requests  # checked, but no new session
    assert request.method == "GET"


@pytest.mark.parametrize("latest", ["declined", "expired", "abandoned"])
def test_start_again_after_an_unsuccessful_session(db_client, db, didit, latest):
    db.on("GET", "users", rows(profile()))
    db.on("GET", "identity_verifications", rows(verification(latest)))
    new_row = verification(id=OTHER_VERIFICATION_ID)
    db.on("POST", "identity_verifications", rows(new_row, status=201))
    didit.replies.append(session())

    response = db_client.post(URL)

    assert response.status_code == 202
    assert response.json()["verification_id"] == str(OTHER_VERIFICATION_ID)


def test_start_before_onboarding(db_client, db, didit):
    db.on("GET", "users", rows())

    response = db_client.post(URL)

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "profile_not_found"


@pytest.mark.parametrize(
    "reply",
    [
        httpx.Response(500),
        httpx.Response(400, json={"detail": "Insufficient credits"}),
        httpx.Response(401),
        httpx.Response(201, json={"status": "Not Started"}),  # no session ID or URL
        httpx.Response(201, json={"session_id": SESSION_ID, "url": SESSION_URL, "status": "?"}),
        httpx.Response(200, text="not json"),
        httpx.ConnectError("down"),
    ],
)
def test_start_when_didit_fails(db_client, db, didit, reply):
    db.on("GET", "users", rows(profile()))
    db.on("GET", "identity_verifications", rows())
    didit.replies.append(reply)

    response = db_client.post(URL)

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "verification_unavailable"
    assert not db.sent("POST", "identity_verifications")


def test_start_when_not_configured(db_client):
    response = db_client.post(URL)

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "verification_unavailable"


# ---------------------------------------------------------------- GET /users/me/verification


def test_get_with_no_session(db_client, db, didit):
    db.on("GET", "users", rows(profile()))
    db.on("GET", "identity_verifications", rows())

    response = db_client.get(URL)

    assert response.status_code == 200
    assert response.json() == {"verified": False, "verification": None}
    assert not didit.requests


def test_get_checks_an_unsettled_session_and_verifies_the_user(db_client, db, didit):
    db.on("GET", "users", rows(profile()))
    db.on("GET", "identity_verifications", rows(verification("in_progress")))
    db.on("PATCH", "identity_verifications", rows(verification("approved")))
    db.on("PATCH", "users", rows({"id": str(ME)}))
    didit.replies.append(decision("Approved"))

    response = db_client.get(URL)

    assert response.status_code == 200
    assert response.json()["verified"] is True
    assert response.json()["verification"]["status"] == "approved"
    [request] = didit.requests
    assert request.method == "GET"
    assert request.url == f"https://didit.example/v3/session/{SESSION_ID}/decision/"
    [verify] = db.sent("PATCH", "users")
    assert verify.url.params["id"] == f"eq.{ME}"
    assert verify.url.params["verified_at"] == "is.null"  # an earlier verification is kept
    assert body(verify) == {"verified_at": "now"}
    # The user is verified before the session is saved as approved.
    [save] = db.sent("PATCH", "identity_verifications")
    assert db.requests.index(verify) < db.requests.index(save)


def test_get_leaves_the_session_unsettled_if_verifying_the_user_fails(db_client, db, didit):
    db.on("GET", "users", rows(profile()))
    db.on("GET", "identity_verifications", rows(verification("in_progress")))
    db.on("PATCH", "users", httpx.Response(503))
    didit.replies.append(decision("Approved"))

    response = db_client.get(URL)

    assert response.status_code == 503
    # Still in_progress, so the next GET asks Didit again and retries.
    assert not db.sent("PATCH", "identity_verifications")


def test_get_records_a_new_status_without_verifying(db_client, db, didit):
    db.on("GET", "users", rows(profile()))
    db.on("GET", "identity_verifications", rows(verification("not_started")))
    db.on("PATCH", "identity_verifications", rows(verification("in_review")))
    didit.replies.append(decision("In Review"))

    response = db_client.get(URL)

    assert response.json()["verified"] is False
    assert response.json()["verification"]["status"] == "in_review"


def test_get_with_an_unchanged_status_writes_nothing(db_client, db, didit):
    db.on("GET", "users", rows(profile()))
    db.on("GET", "identity_verifications", rows(verification("in_progress")))
    didit.replies.append(decision("In Progress"))

    assert db_client.get(URL).json()["verification"]["status"] == "in_progress"


@pytest.mark.parametrize("settled", ["approved", "declined", "expired", "abandoned", "kyc_expired"])
def test_get_does_not_ask_about_a_settled_session(db_client, db, didit, settled):
    db.on("GET", "users", rows(profile(verified=settled == "approved")))
    db.on("GET", "identity_verifications", rows(verification(settled)))

    response = db_client.get(URL)

    assert response.json()["verified"] is (settled == "approved")
    assert response.json()["verification"]["status"] == settled
    assert not didit.requests


@pytest.mark.parametrize(
    "reply", [httpx.Response(503), httpx.ConnectError("down"), decision("Something New")]
)
def test_get_shows_the_stored_status_when_didit_cannot_answer(db_client, db, didit, reply):
    db.on("GET", "users", rows(profile()))
    db.on("GET", "identity_verifications", rows(verification("in_progress")))
    didit.replies.append(reply)

    response = db_client.get(URL)

    assert response.status_code == 200
    assert response.json()["verification"]["status"] == "in_progress"


def test_get_ignores_a_session_for_another_user(db_client, db, didit):
    db.on("GET", "users", rows(profile()))
    db.on("GET", "identity_verifications", rows(verification("in_progress")))
    didit.replies.append(decision("Approved", vendor_data=str(UUID(int=99))))

    response = db_client.get(URL)

    assert response.json()["verified"] is False
    assert response.json()["verification"]["status"] == "in_progress"


def test_get_without_didit_configured_shows_the_stored_status(db_client, db):
    db.on("GET", "users", rows(profile()))
    db.on("GET", "identity_verifications", rows(verification("in_progress")))

    response = db_client.get(URL)

    assert response.status_code == 200
    assert response.json()["verification"]["status"] == "in_progress"


def test_get_before_onboarding(db_client, db):
    db.on("GET", "users", rows())

    response = db_client.get(URL)

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "profile_not_found"


# ---------------------------------------------------------------- statuses


@pytest.mark.parametrize(
    ("didit_status", "stored"),
    [
        ("Not Started", "not_started"),
        ("In Progress", "in_progress"),
        ("In Review", "in_review"),
        ("Awaiting User", "awaiting_user"),
        ("Resubmitted", "resubmitted"),
        ("Approved", "approved"),
        ("APPROVED", "approved"),
        ("Declined", "declined"),
        ("Expired", "expired"),
        ("Abandoned", "abandoned"),
        ("Kyc Expired", "kyc_expired"),
        ("KYC Expired", "kyc_expired"),
        ("Unknown", None),
        (None, None),
        (3, None),
    ],
)
def test_normalize_status(didit_status, stored):
    assert normalize_status(didit_status) == stored
