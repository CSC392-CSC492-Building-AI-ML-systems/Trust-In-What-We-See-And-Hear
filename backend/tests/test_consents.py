import asyncio
from datetime import UTC, datetime
from uuid import UUID

import httpx
import pytest

from app.core import clock
from app.integrations.supabase import SupabaseDatabase
from app.modules.consents.service import ConsentNotUsableError, use_consent
from supabase_mock import (
    RoutedSupabaseDb,
    body,
    postgres_error,
    rows,
    user_row,
)

ME = UUID(int=1)  # matches the test_user fixture
FRIEND = UUID(int=2)
CONSENT_ID = UUID(int=200)
NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
NOW_ISO = "2026-11-10T12:00:00.000000Z"

FRIENDSHIP = {"id": "f", "status": "accepted", "requester_id": str(ME), "addressee_id": str(FRIEND)}


def friends_reply(request):
    return rows(FRIENDSHIP)


def not_friends_reply(request):
    return rows()


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    monkeypatch.setattr(clock, "now", lambda: NOW)


def consent(requester=ME, consenter=FRIEND, status="pending", **changes):
    users = {ME: user_row(ME, "alex_morgan"), FRIEND: user_row(FRIEND, "sam_lee", True)}
    return {
        "id": str(CONSENT_ID),
        "status": status,
        "requested_at": "2026-11-10T11:00:00+00:00",
        "responded_at": None,
        "expires_at": None,
        "used_at": None,
        "requester_id": str(requester),
        "consenter_id": str(consenter),
        "requester": users[requester],
        "consenter": users[consenter],
        **changes,
    }


def voice(status="ready"):
    return rows({"status": status})


# POST /consents


def test_request_consent(db_client, db):
    db.on("GET", "friendships", friends_reply)
    db.on("GET", "voices", voice())
    db.on("GET", "scam_consents", rows())
    db.on("POST", "scam_consents", rows(consent(), status=201))

    response = db_client.post("/api/v1/consents", json={"consenter_user_id": str(FRIEND)})

    assert response.status_code == 201
    data = response.json()
    assert data["direction"] == "outgoing"
    assert data["user"]["username"] == "sam_lee"
    assert data["status"] == "pending"
    assert data["usable"] is False
    assert body(db.sent("POST", "scam_consents")[0]) == {
        "requester_id": str(ME),
        "consenter_id": str(FRIEND),
    }
    assert db.sent("GET", "voices")[0].url.params["user_id"] == f"eq.{FRIEND}"
    available = db.sent("GET", "scam_consents")[0].url.params
    assert available["used_at"] == "is.null"
    assert available["expires_at"] == f"gt.{NOW_ISO}"


def test_request_consent_from_self_is_400(db_client, db):
    response = db_client.post("/api/v1/consents", json={"consenter_user_id": str(ME)})
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "cannot_request_self"


def test_request_consent_from_non_friend_is_409(db_client, db):
    db.on("GET", "friendships", not_friends_reply)
    response = db_client.post("/api/v1/consents", json={"consenter_user_id": str(FRIEND)})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "not_friends"


def test_pending_friend_request_is_not_a_friendship(db_client, db):
    db.on("GET", "friendships", rows({"status": "pending", "requester_id": str(ME)}))
    response = db_client.post("/api/v1/consents", json={"consenter_user_id": str(FRIEND)})
    assert response.json()["detail"]["code"] == "not_friends"


@pytest.mark.parametrize("voice_rows", [rows(), voice("uploaded"), voice("failed")])
def test_request_consent_needs_ready_voice(db_client, db, voice_rows):
    db.on("GET", "friendships", friends_reply)
    db.on("GET", "voices", voice_rows)
    response = db_client.post("/api/v1/consents", json={"consenter_user_id": str(FRIEND)})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "voice_not_ready"


def test_request_consent_while_one_is_available_is_409(db_client, db):
    db.on("GET", "friendships", friends_reply)
    db.on("GET", "voices", voice())
    db.on("GET", "scam_consents", rows({"id": str(CONSENT_ID)}))
    response = db_client.post("/api/v1/consents", json={"consenter_user_id": str(FRIEND)})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "consent_available"


def test_request_consent_while_one_is_pending_is_409(db_client, db):
    db.on("GET", "friendships", friends_reply)
    db.on("GET", "voices", voice())
    db.on("GET", "scam_consents", rows())
    db.on("POST", "scam_consents", postgres_error("23505", "scam_consents_one_pending_idx"))
    response = db_client.post("/api/v1/consents", json={"consenter_user_id": str(FRIEND)})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "consent_request_pending"


# GET /consents and /consents/requests


def test_list_usable_consents(db_client, db):
    accepted = consent(
        status="accepted",
        responded_at="2026-11-10T11:30:00+00:00",
        expires_at="2026-11-11T11:30:00+00:00",
    )
    db.on("GET", "scam_consents", rows(accepted))

    response = db_client.get("/api/v1/consents")

    assert response.status_code == 200
    [item] = response.json()["items"]
    assert item["usable"] is True
    params = db.sent("GET", "scam_consents")[0].url.params
    assert params["requester_id"] == f"eq.{ME}"
    assert params["status"] == "eq.accepted"
    assert params["used_at"] == "is.null"
    assert params["expires_at"] == f"gt.{NOW_ISO}"


def test_list_incoming_consent_requests(db_client, db):
    db.on("GET", "scam_consents", rows(consent(requester=FRIEND, consenter=ME)))
    response = db_client.get("/api/v1/consents/requests?direction=incoming")
    assert response.status_code == 200
    [item] = response.json()["items"]
    assert item["direction"] == "incoming"
    assert item["user"]["username"] == "sam_lee"
    params = db.sent("GET", "scam_consents")[0].url.params
    assert params["consenter_id"] == f"eq.{ME}"
    assert params["status"] == "eq.pending"


# POST /consents/{id}/accept, /decline and /revoke


def test_accept_consent_sets_expiry(db_client, db):
    db.on("GET", "scam_consents", rows({"requester_id": str(FRIEND)}))
    db.on("GET", "friendships", friends_reply)
    accepted = consent(
        requester=FRIEND,
        consenter=ME,
        status="accepted",
        responded_at=NOW.isoformat(),
        expires_at="2026-11-11T12:00:00+00:00",
    )
    db.on("PATCH", "scam_consents", rows(accepted))

    response = db_client.post(f"/api/v1/consents/{CONSENT_ID}/accept")

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    request = db.sent("PATCH", "scam_consents")[0]
    # Only the consenter can accept, and only a pending request.
    assert request.url.params["consenter_id"] == f"eq.{ME}"
    assert request.url.params["status"] == "eq.pending"
    assert body(request) == {
        "status": "accepted",
        "responded_at": NOW_ISO,
        "expires_at": "2026-11-11T12:00:00.000000Z",  # default: 24 hours
    }


def test_accept_consent_uses_configured_period(application, db_client, db):
    application.state.settings.consent_valid_minutes = 30
    db.on("GET", "scam_consents", rows({"requester_id": str(FRIEND)}))
    db.on("GET", "friendships", friends_reply)
    db.on(
        "PATCH",
        "scam_consents",
        rows(consent(FRIEND, ME, "accepted", expires_at="2026-11-10T12:30:00+00:00")),
    )
    db_client.post(f"/api/v1/consents/{CONSENT_ID}/accept")
    assert body(db.sent("PATCH", "scam_consents")[0])["expires_at"] == (
        "2026-11-10T12:30:00.000000Z"
    )


def test_accept_someone_elses_consent_is_404(db_client, db):
    db.on("GET", "scam_consents", rows())
    response = db_client.post(f"/api/v1/consents/{CONSENT_ID}/accept")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "consent_not_found"
    assert db.sent("GET", "scam_consents")[0].url.params["consenter_id"] == f"eq.{ME}"


def test_accept_consent_after_unfriending_is_409(db_client, db):
    db.on("GET", "scam_consents", rows({"requester_id": str(FRIEND)}))
    db.on("GET", "friendships", not_friends_reply)
    response = db_client.post(f"/api/v1/consents/{CONSENT_ID}/accept")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "not_friends"


def test_decline_consent(db_client, db):
    declined = consent(FRIEND, ME, "declined", responded_at=NOW.isoformat())
    db.on("PATCH", "scam_consents", rows(declined))
    response = db_client.post(f"/api/v1/consents/{CONSENT_ID}/decline")
    assert response.status_code == 200
    assert response.json()["status"] == "declined"
    request = db.sent("PATCH", "scam_consents")[0]
    assert request.url.params["consenter_id"] == f"eq.{ME}"
    assert body(request) == {"status": "declined", "responded_at": NOW_ISO}


def test_decline_unknown_consent_is_404(db_client, db):
    db.on("PATCH", "scam_consents", rows())
    assert db_client.post(f"/api/v1/consents/{CONSENT_ID}/decline").status_code == 404


def test_revoke_consent_expires_it_now(db_client, db):
    revoked = consent(FRIEND, ME, "accepted", expires_at=NOW.isoformat())
    db.on("PATCH", "scam_consents", rows(revoked))
    response = db_client.post(f"/api/v1/consents/{CONSENT_ID}/revoke")
    assert response.status_code == 200
    assert response.json()["usable"] is False
    request = db.sent("PATCH", "scam_consents")[0]
    assert request.url.params["consenter_id"] == f"eq.{ME}"
    assert request.url.params["used_at"] == "is.null"
    assert body(request) == {"expires_at": NOW_ISO}


def test_revoke_unknown_consent_is_404(db_client, db):
    db.on("PATCH", "scam_consents", rows())
    assert db_client.post(f"/api/v1/consents/{CONSENT_ID}/revoke").status_code == 404


# use_consent (for the calls module)


def run_use_consent(fake: RoutedSupabaseDb):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(fake)) as http_client:
            database = SupabaseDatabase("https://example.supabase.co", "secret", http_client)
            return await use_consent(database, CONSENT_ID, ME)

    return asyncio.run(run())


def test_use_consent_marks_it_used(db):
    db.on("GET", "scam_consents", rows({"id": str(CONSENT_ID), "consenter_id": str(FRIEND)}))
    db.on("GET", "friendships", friends_reply)
    db.on("GET", "voices", voice())
    used = consent(status="accepted", used_at=NOW.isoformat())
    db.on("PATCH", "scam_consents", rows(used))

    assert run_use_consent(db) == used
    request = db.sent("PATCH", "scam_consents")[0]
    # The update itself re-checks the conditions, so two calls cannot both use it.
    assert request.url.params["requester_id"] == f"eq.{ME}"
    assert request.url.params["used_at"] == "is.null"
    assert request.url.params["expires_at"] == f"gt.{NOW_ISO}"
    assert body(request) == {"used_at": NOW_ISO}


@pytest.mark.parametrize(
    ("replies", "code"),
    [
        ([("GET", "scam_consents", rows())], "consent_unusable"),
        (
            [
                ("GET", "scam_consents", rows({"id": "c", "consenter_id": str(FRIEND)})),
                ("GET", "friendships", not_friends_reply),
            ],
            "not_friends",
        ),
        (
            [
                ("GET", "scam_consents", rows({"id": "c", "consenter_id": str(FRIEND)})),
                ("GET", "friendships", friends_reply),
                ("GET", "voices", voice("uploaded")),
            ],
            "voice_not_ready",
        ),
        (
            [
                ("GET", "scam_consents", rows({"id": "c", "consenter_id": str(FRIEND)})),
                ("GET", "friendships", friends_reply),
                ("GET", "voices", voice()),
                ("PATCH", "scam_consents", rows()),  # used by another call meanwhile
            ],
            "consent_unusable",
        ),
    ],
)
def test_use_consent_failures(db, replies, code):
    for method, table, reply in replies:
        db.on(method, table, reply)
    with pytest.raises(ConsentNotUsableError) as error:
        run_use_consent(db)
    assert error.value.code == code
