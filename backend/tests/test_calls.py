"""Tests for the call endpoints and the voice pipeline's internal call endpoints.

AI-assisted code (written with Claude Code), cited per the CSC398 AI policy.
"""

import hashlib
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import httpx
import pytest
from pydantic import SecretStr

from app.core import clock
from app.integrations.supabase import SupabaseStorage, get_supabase_storage
from supabase_mock import body, postgres_error, rows, user_row

ME = UUID(int=1)  # matches the test_user fixture
FRIEND = UUID(int=2)
CONSENT_ID = UUID(int=200)
SCENARIO_ID = UUID(int=300)
CALL_ID = UUID(int=400)
NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
SECRET = "pipeline-secret"
PIPELINE = {"Authorization": f"Bearer {SECRET}"}
TOKEN = "the-token"
SAMPLE_PATH = f"{FRIEND}/sample.wav"


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    monkeypatch.setattr(clock, "now", lambda: NOW)


@pytest.fixture(autouse=True)
def pipeline_settings(application):
    settings = application.state.settings
    settings.pipeline_ws_url = "wss://pipeline.example/"
    settings.pipeline_backend_secret = SecretStr(SECRET)


@pytest.fixture
def storage_requests():
    return []


@pytest.fixture
def api(application, db_client, storage_requests):
    def sign(request):
        storage_requests.append(request)
        path = request.url.path.removeprefix("/storage/v1/object/sign/")
        return httpx.Response(200, json={"signedURL": f"/object/sign/{path}?token=signed"})

    async def override():
        async with httpx.AsyncClient(transport=httpx.MockTransport(sign)) as http_client:
            yield SupabaseStorage("https://example.supabase.co", "secret", http_client)

    application.dependency_overrides[get_supabase_storage] = override
    yield db_client
    application.dependency_overrides.pop(get_supabase_storage, None)


def scenario(**changes):
    return {
        "id": str(SCENARIO_ID),
        "title": "Stuck abroad",
        "description": "Your friend says they lost their wallet abroad.",
        "ask": "Send $300 by e-transfer.",
        "red_flags": ["urgency", "secrecy"],
        "max_duration_seconds": 300,
        **changes,
    }


def consent():
    return {
        "id": str(CONSENT_ID),
        "status": "accepted",
        "requested_at": "2026-11-10T11:00:00+00:00",
        "responded_at": "2026-11-10T11:30:00+00:00",
        "expires_at": "2026-11-11T11:30:00+00:00",
        "used_at": None,
        "requester_id": str(ME),
        "consenter_id": str(FRIEND),
        "requester": user_row(ME, "alex_morgan"),
        "consenter": {**user_row(FRIEND, "sam_lee", True), "voice": {"status": "ready"}},
    }


def call(status="queued", result=None, **changes):
    return {
        "id": str(CALL_ID),
        "consent_id": str(CONSENT_ID),
        "scenario_id": str(SCENARIO_ID),
        "persona_name": "Sam Lee",
        "scenario_title": "Stuck abroad",
        "status": status,
        "created_at": "2026-11-10T12:00:00+00:00",
        "started_at": None,
        "ended_at": None,
        "duration_seconds": None,
        "failure_reason": None,
        "result": result,
        **changes,
    }


RESULT = {
    "outcome": "recognized_scam",
    "score": 85,
    "summary": "You questioned who was calling.",
    "did_well": ["Asked the caller to prove who they were."],
    "to_improve": ["Call the person back on a number you know."],
    "red_flags_noticed": ["urgency"],
    "red_flags_missed": ["secrecy"],
    "sensitive_info_shared": False,
}


def expect_call_start(db, scenario_row=None):
    """Queue the queries of a successful POST /calls."""
    db.on("GET", "scam_scenarios", rows(scenario_row or scenario()))
    db.on("GET", "scam_consents", rows(consent()))  # read before anything is used
    db.on("GET", "scam_consents", rows({"id": str(CONSENT_ID), "consenter_id": str(FRIEND)}))
    db.on("GET", "voices", rows({"status": "ready"}))
    db.on("PATCH", "scam_consents", rows(consent()))
    db.on("POST", "calls", rows(call(), status=201))


# POST /calls


def test_start_call(api, db):
    expect_call_start(db)

    response = api.post(
        "/api/v1/calls", json={"consent_id": str(CONSENT_ID), "scenario_id": str(SCENARIO_ID)}
    )

    assert response.status_code == 202
    data = response.json()
    assert data["status"] == "queued"
    assert data["persona_name"] == "Sam Lee"
    assert data["outcome"] is None
    ws_url = urlsplit(data["ws_url"])
    assert f"{ws_url.scheme}://{ws_url.netloc}{ws_url.path}" == (
        f"wss://pipeline.example/ws/calls/{CALL_ID}"
    )
    token = parse_qs(ws_url.query)["token"][0]
    assert len(token) >= 43  # 32 random bytes

    inserted = body(db.sent("POST", "calls")[0])
    # Only the hash of the token is stored.
    assert inserted["claim_token_hash"] == hashlib.sha256(token.encode()).hexdigest()
    assert token not in str(inserted)
    assert inserted["claim_expires_at"] == "2026-11-10T12:02:00.000000Z"
    assert inserted["user_id"] == str(ME)
    assert inserted["persona_user_id"] == str(FRIEND)
    assert inserted["persona_name"] == "Sam Lee"
    assert inserted["status"] == "queued"
    assert inserted["max_duration_seconds"] == 300
    assert inserted["call_context"] == {
        "persona": {"name": "Sam Lee", "relationship": "friend"},
        "target": {"name": "Alex Morgan", "called_as": "Alex"},
        "scenario": {
            "title": "Stuck abroad",
            "situation": "Your friend says they lost their wallet abroad.",
            "ask": "Send $300 by e-transfer.",
            "tactics": ["urgency", "secrecy"],
        },
        "details": {},  # whose onboarding answers to use is not decided
    }
    # The consent is used up, by the current user only.
    used = db.sent("PATCH", "scam_consents")[0]
    assert used.url.params["requester_id"] == f"eq.{ME}"
    assert body(used) == {"used_at": "now"}
    assert db.sent("GET", "scam_scenarios")[0].url.params["id"] == f"eq.{SCENARIO_ID}"


def test_start_call_without_pipeline_is_503(api, db, application):
    application.state.settings.pipeline_ws_url = None
    response = api.post(
        "/api/v1/calls", json={"consent_id": str(CONSENT_ID), "scenario_id": str(SCENARIO_ID)}
    )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "calls_unavailable"
    assert db.requests == []


def test_start_call_with_hidden_scenario_is_404(api, db):
    db.on("GET", "scam_scenarios", rows())
    response = api.post(
        "/api/v1/calls", json={"consent_id": str(CONSENT_ID), "scenario_id": str(SCENARIO_ID)}
    )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "scenario_not_found"


def test_start_call_with_unusable_consent_is_409(api, db):
    db.on("GET", "scam_scenarios", rows(scenario()))
    db.on("GET", "scam_consents", rows())
    response = api.post(
        "/api/v1/calls", json={"consent_id": str(CONSENT_ID), "scenario_id": str(SCENARIO_ID)}
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "consent_unusable"
    params = db.sent("GET", "scam_consents")[0].url.params
    assert params["requester_id"] == f"eq.{ME}"
    assert params["used_at"] == "is.null"


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"ask": None}, "scenario_incomplete"),  # created before `ask` existed
        ({"description": "x" * 501}, "call_context_invalid"),
    ],
)
def test_start_call_with_unusable_scenario_keeps_the_consent(api, db, changes, code):
    db.on("GET", "scam_scenarios", rows(scenario(**changes)))
    db.on("GET", "scam_consents", rows(consent()))
    response = api.post(
        "/api/v1/calls", json={"consent_id": str(CONSENT_ID), "scenario_id": str(SCENARIO_ID)}
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == code
    assert db.sent("PATCH", "scam_consents") == []  # not used up


@pytest.mark.parametrize(
    ("use_replies", "code"),
    [
        ([rows()], "consent_unusable"),  # used by a concurrent request
        (
            [rows({"id": str(CONSENT_ID), "consenter_id": str(FRIEND)})],
            "voice_not_ready",
        ),
    ],
)
def test_start_call_when_consent_cannot_be_used(api, db, use_replies, code):
    db.on("GET", "scam_scenarios", rows(scenario()))
    db.on("GET", "scam_consents", rows(consent()), *use_replies)
    if code == "voice_not_ready":
        db.on("GET", "voices", rows({"status": "failed"}))
    response = api.post(
        "/api/v1/calls", json={"consent_id": str(CONSENT_ID), "scenario_id": str(SCENARIO_ID)}
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == code
    assert db.sent("POST", "calls") == []


# GET /calls and /calls/{id}


def test_call_history(api, db):
    db.on("PATCH", "calls", rows())
    db.on(
        "GET",
        "calls",
        rows(
            call("completed", RESULT, duration_seconds=42),
            call("completed", {**RESULT, "outcome": "inconclusive", "score": 0}),
            call("in_progress"),
        ),
    )

    response = api.get("/api/v1/calls?limit=5&offset=10")

    assert response.status_code == 200
    data = response.json()
    assert (data["limit"], data["offset"]) == (5, 10)
    scored, inconclusive, ongoing = data["items"]
    assert (scored["outcome"], scored["score"], scored["duration_seconds"]) == (
        "recognized_scam", 85, 42
    )
    assert (inconclusive["outcome"], inconclusive["score"]) == ("inconclusive", None)
    assert (ongoing["status"], ongoing["outcome"]) == ("in_progress", None)
    params = db.sent("GET", "calls")[0].url.params
    assert params["user_id"] == f"eq.{ME}"
    assert params["order"] == "created_at.desc,id"
    assert (params["limit"], params["offset"]) == ("5", "10")


def test_reading_calls_fails_stale_ones(api, db):
    db.on("PATCH", "calls", rows())
    db.on("GET", "calls", rows())

    api.get("/api/v1/calls")

    stale = db.sent("PATCH", "calls")[0]
    assert stale.url.params["user_id"] == f"eq.{ME}"
    assert stale.url.params["or"] == (
        "(and(status.eq.queued,claim_expires_at.lt.now),"
        "and(status.eq.in_progress,started_at.lt.2026-11-10T11:45:00.000000Z))"
    )
    assert body(stale) == {
        "status": "failed",
        "failure_reason": "timed_out",
        "ended_at": "now",
        "claim_token_hash": None,
        "call_context": None,
    }


def test_call_details(api, db):
    db.on("PATCH", "calls", rows())
    db.on("GET", "calls", rows(call("completed", [RESULT], duration_seconds=42)))

    response = api.get(f"/api/v1/calls/{CALL_ID}")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "completed"
    assert data["summary"] == {
        **RESULT,
        "duration_seconds": 42,
    }
    assert data["failure_reason"] is None
    params = db.sent("GET", "calls")[0].url.params
    assert params["id"] == f"eq.{CALL_ID}"
    assert params["user_id"] == f"eq.{ME}"  # only the current user's calls


@pytest.mark.parametrize(
    ("row", "failure_reason"),
    [(call("queued"), None), (call("failed", failure_reason="timed_out"), "timed_out")],
)
def test_unfinished_call_has_no_summary(api, db, row, failure_reason):
    db.on("PATCH", "calls", rows())
    db.on("GET", "calls", rows(row))
    data = api.get(f"/api/v1/calls/{CALL_ID}").json()
    assert data["summary"] is None
    assert data["failure_reason"] == failure_reason


def test_other_users_call_is_404(api, db):
    db.on("PATCH", "calls", rows())
    db.on("GET", "calls", rows())
    response = api.get(f"/api/v1/calls/{CALL_ID}")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "call_not_found"


# Internal: authentication


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"}])
def test_internal_routes_need_the_pipeline_secret(api, db, headers):
    for path in ("claim", "result"):
        response = api.post(f"/api/v1/internal/calls/{CALL_ID}/{path}", json={}, headers=headers)
        assert response.status_code == 401
    assert db.requests == []


def test_user_token_is_not_the_pipeline_secret(api, db):
    response = api.post(
        f"/api/v1/internal/calls/{CALL_ID}/claim",
        json={"token": TOKEN},
        headers={"Authorization": "Bearer a-user-access-token"},
    )
    assert response.status_code == 401


def test_internal_routes_without_secret_configured_are_503(api, db, application):
    application.state.settings.pipeline_backend_secret = None
    response = api.post(
        f"/api/v1/internal/calls/{CALL_ID}/claim", json={"token": TOKEN}, headers=PIPELINE
    )
    assert response.status_code == 503


# Internal: claim


def stored_call(**changes):
    return {
        "id": str(CALL_ID),
        "persona_user_id": str(FRIEND),
        "call_context": {"persona": {"name": "Sam Lee"}},
        "max_duration_seconds": 300,
        **changes,
    }


def test_claim_call(api, db, storage_requests):
    db.on("GET", "calls", rows(stored_call()))
    db.on("PATCH", "calls", rows({"id": str(CALL_ID)}))
    db.on("GET", "voices", rows({"status": "ready", "sample_storage_path": SAMPLE_PATH}))

    response = api.post(
        f"/api/v1/internal/calls/{CALL_ID}/claim", json={"token": TOKEN}, headers=PIPELINE
    )

    assert response.status_code == 200
    assert response.json() == {
        "context": {"persona": {"name": "Sam Lee"}},
        "voice_url": (
            f"https://example.supabase.co/storage/v1/object/sign/voice-samples/{SAMPLE_PATH}"
            "?token=signed"
        ),
        "max_duration_seconds": 300,
    }
    claim = db.sent("PATCH", "calls")[0]
    assert claim.url.params["status"] == "eq.queued"
    assert claim.url.params["claim_token_hash"] == (
        f"eq.{hashlib.sha256(TOKEN.encode()).hexdigest()}"
    )
    assert claim.url.params["claim_expires_at"] == "gt.now"
    assert body(claim) == {
        "status": "in_progress",
        "started_at": "now",
        "claim_token_hash": None,  # single use
        "call_context": None,
    }
    assert db.sent("GET", "voices")[0].url.params["user_id"] == f"eq.{FRIEND}"
    assert body(storage_requests[0]) == {"expiresIn": 300}


def test_claim_unknown_call_is_404(api, db):
    db.on("GET", "calls", rows())
    response = api.post(
        f"/api/v1/internal/calls/{CALL_ID}/claim", json={"token": TOKEN}, headers=PIPELINE
    )
    assert response.status_code == 404


def test_claim_with_wrong_used_or_expired_token_is_403(api, db):
    db.on("GET", "calls", rows(stored_call()))
    db.on("PATCH", "calls", rows())
    response = api.post(
        f"/api/v1/internal/calls/{CALL_ID}/claim", json={"token": TOKEN}, headers=PIPELINE
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "token_rejected"


@pytest.mark.parametrize(
    ("persona", "voices"),
    [
        (str(FRIEND), rows()),  # voice deleted
        (str(FRIEND), rows({"status": "failed", "sample_storage_path": SAMPLE_PATH})),
        (None, None),  # the persona deleted their account
    ],
)
def test_claim_when_voice_is_gone_fails_the_call(api, db, persona, voices):
    db.on("GET", "calls", rows(stored_call(persona_user_id=persona)))
    db.on("PATCH", "calls", rows({"id": str(CALL_ID)}), rows({"id": str(CALL_ID)}))
    if voices is not None:
        db.on("GET", "voices", voices)
    response = api.post(
        f"/api/v1/internal/calls/{CALL_ID}/claim", json={"token": TOKEN}, headers=PIPELINE
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "voice_unavailable"
    failed = db.sent("PATCH", "calls")[1]
    assert failed.url.params["status"] == "eq.in_progress"
    assert body(failed) == {
        "status": "failed",
        "failure_reason": "voice_unavailable",
        "ended_at": "now",
    }


# Internal: result


def report(ended_by="hangup", result=RESULT):
    return {
        "ended_by": ended_by,
        "duration_seconds": 42,
        "result": result and {**result, "model_version": "qwen3-8b/score-v1"},
    }


def test_report_result(api, db):
    db.on("GET", "calls", rows({"id": str(CALL_ID), "status": "in_progress"}))
    db.on("POST", "call_results", rows({"call_id": str(CALL_ID)}, status=201))
    db.on("PATCH", "calls", rows({"id": str(CALL_ID)}))

    response = api.post(
        f"/api/v1/internal/calls/{CALL_ID}/result", json=report(), headers=PIPELINE
    )

    assert response.status_code == 204
    assert body(db.sent("POST", "call_results")[0]) == {
        "call_id": str(CALL_ID),
        **RESULT,
        "model_version": "qwen3-8b/score-v1",
    }
    finished = db.sent("PATCH", "calls")[0]
    assert finished.url.params["status"] == "eq.in_progress"
    assert body(finished) == {
        "status": "completed",
        "failure_reason": None,
        "ended_at": "now",
        "duration_seconds": 42,
        "call_context": None,
    }


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        (report(result=None), "not_scored"),
        (report("error", result=None), "error"),
        (report("error"), "error"),  # an error call is failed even if it was scored
    ],
)
def test_report_without_usable_result_fails_the_call(api, db, payload, reason):
    db.on("GET", "calls", rows({"id": str(CALL_ID), "status": "in_progress"}))
    db.on("PATCH", "calls", rows({"id": str(CALL_ID)}))
    response = api.post(f"/api/v1/internal/calls/{CALL_ID}/result", json=payload, headers=PIPELINE)
    assert response.status_code == 204
    update = body(db.sent("PATCH", "calls")[0])
    assert (update["status"], update["failure_reason"]) == ("failed", reason)
    assert db.sent("POST", "call_results") == []


def test_report_sent_twice_is_accepted(api, db):
    # The first attempt stored the result, but its answer was lost.
    db.on("GET", "calls", rows({"id": str(CALL_ID), "status": "in_progress"}))
    db.on("POST", "call_results", postgres_error("23505", "call_results_pkey"))
    db.on("PATCH", "calls", rows({"id": str(CALL_ID)}))
    response = api.post(f"/api/v1/internal/calls/{CALL_ID}/result", json=report(), headers=PIPELINE)
    assert response.status_code == 204


@pytest.mark.parametrize("status", ["completed", "failed"])
def test_report_for_finished_call_changes_nothing(api, db, status):
    db.on("GET", "calls", rows({"id": str(CALL_ID), "status": status}))
    response = api.post(f"/api/v1/internal/calls/{CALL_ID}/result", json=report(), headers=PIPELINE)
    assert response.status_code == 204
    assert [r.method for r in db.requests] == ["GET"]


def test_report_for_unclaimed_call_is_409(api, db):
    db.on("GET", "calls", rows({"id": str(CALL_ID), "status": "queued"}))
    response = api.post(f"/api/v1/internal/calls/{CALL_ID}/result", json=report(), headers=PIPELINE)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "call_not_in_progress"


def test_report_for_unknown_call_is_404(api, db):
    db.on("GET", "calls", rows())
    response = api.post(f"/api/v1/internal/calls/{CALL_ID}/result", json=report(), headers=PIPELINE)
    assert response.status_code == 404


@pytest.mark.parametrize(
    "changes",
    [
        {"ended_by": "exploded"},
        {"result": {**RESULT, "score": 101}},
        {"result": {**RESULT, "outcome": "won"}},
        {"duration_seconds": -1},
    ],
)
def test_report_rejects_invalid_bodies(api, db, changes):
    response = api.post(
        f"/api/v1/internal/calls/{CALL_ID}/result", json={**report(), **changes}, headers=PIPELINE
    )
    assert response.status_code == 422
