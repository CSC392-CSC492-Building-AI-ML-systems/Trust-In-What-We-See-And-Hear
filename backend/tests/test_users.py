import json
from uuid import UUID

import httpx
import pytest

from app.integrations.supabase import SupabaseDatabase, get_supabase_db

USER_ID = UUID(int=1)  # matches the test_user fixture
PROFILE_ROW = {
    "id": str(USER_ID),
    "username": "alex_morgan",
    "display_name": "Alex Morgan",
    "preferred_language": "en",
    "verified_at": None,
    "onboarding_answers": {"age_range": "25-34"},
    "created_at": "2026-11-07T12:00:00+00:00",
}
PROFILE = {
    "user_id": str(USER_ID),
    "username": "alex_morgan",
    "display_name": "Alex Morgan",
    "preferred_language": "en",
    "verified": False,
    "onboarding_answers": {"age_range": "25-34"},
    "created_at": "2026-11-07T12:00:00Z",
}
ONBOARDING = {
    "username": "alex_morgan",
    "display_name": "Alex Morgan",
    "accept_privacy_policy": True,
    "answers": {"age_range": "25-34"},
}


def unique_violation(constraint):
    return httpx.Response(
        409,
        json={
            "code": "23505",
            "message": f'duplicate key value violates unique constraint "{constraint}"',
        },
    )


class SupabaseDbMock:
    """Set `handler` in a test to control what the mocked Supabase database returns."""

    def __init__(self):
        self.handler = lambda request: httpx.Response(500)
        self.requests: list[httpx.Request] = []

    def __call__(self, request):
        self.requests.append(request)
        return self.handler(request)


@pytest.fixture
def supabase_db():
    return SupabaseDbMock()


@pytest.fixture
def users_client(application, authenticated_client, supabase_db):
    async def override():
        transport = httpx.MockTransport(supabase_db)
        async with httpx.AsyncClient(transport=transport) as http_client:
            yield SupabaseDatabase("https://example.supabase.co", "secret", http_client)

    application.dependency_overrides[get_supabase_db] = override
    yield authenticated_client
    application.dependency_overrides.pop(get_supabase_db, None)


# GET /users/me


def test_get_profile(users_client, supabase_db):
    supabase_db.handler = lambda request: httpx.Response(200, json=[PROFILE_ROW])
    response = users_client.get("/api/v1/users/me")
    assert response.status_code == 200
    assert response.json() == PROFILE

    request = supabase_db.requests[0]
    assert request.method == "GET"
    assert request.url.path == "/rest/v1/users"
    assert request.url.params["id"] == f"eq.{USER_ID}"
    assert request.headers["apikey"] == "secret"
    assert request.headers["Authorization"] == "Bearer secret"


def test_get_profile_marks_verified_users(users_client, supabase_db):
    row = {**PROFILE_ROW, "verified_at": "2026-11-08T09:00:00+00:00"}
    supabase_db.handler = lambda request: httpx.Response(200, json=[row])
    assert users_client.get("/api/v1/users/me").json()["verified"] is True


def test_get_profile_before_onboarding_is_404(users_client, supabase_db):
    supabase_db.handler = lambda request: httpx.Response(200, json=[])
    assert users_client.get("/api/v1/users/me").status_code == 404


# POST /users/me/onboarding


def test_onboarding_creates_profile(users_client, supabase_db):
    supabase_db.handler = lambda request: httpx.Response(201, json=[PROFILE_ROW])
    response = users_client.post("/api/v1/users/me/onboarding", json=ONBOARDING)
    assert response.status_code == 201
    assert response.json() == PROFILE

    request = supabase_db.requests[0]
    assert request.method == "POST"
    assert request.headers["Prefer"] == "return=representation"
    row = json.loads(request.content)
    # The user ID comes from the token, never from the request body.
    assert row["id"] == str(USER_ID)
    assert row["username"] == "alex_morgan"
    assert row["onboarding_answers"] == {"age_range": "25-34"}
    assert row["privacy_policy_version"] == "2026-11"
    assert row["privacy_policy_accepted_at"]


@pytest.mark.parametrize(
    ("constraint", "code"),
    [("users_pkey", "profile_exists"), ("users_username_key", "username_taken")],
)
def test_onboarding_conflicts(users_client, supabase_db, constraint, code):
    supabase_db.handler = lambda request: unique_violation(constraint)
    response = users_client.post("/api/v1/users/me/onboarding", json=ONBOARDING)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == code


@pytest.mark.parametrize(
    "changes",
    [
        {"accept_privacy_policy": False},
        {"username": "Alex"},  # uppercase
        {"username": "ab"},  # too short
        {"username": "alex morgan"},
        {"display_name": "   "},
        {"user_id": str(UUID(int=2))},  # unknown fields are rejected
    ],
)
def test_onboarding_rejects_invalid_requests(users_client, supabase_db, changes):
    response = users_client.post(
        "/api/v1/users/me/onboarding", json={**ONBOARDING, **changes}
    )
    assert response.status_code == 422
    assert supabase_db.requests == []


def test_onboarding_requires_privacy_policy(users_client, supabase_db):
    body = {key: value for key, value in ONBOARDING.items() if key != "accept_privacy_policy"}
    response = users_client.post("/api/v1/users/me/onboarding", json=body)
    assert response.status_code == 422


# PUT /users/me


def test_update_sends_only_the_given_fields(users_client, supabase_db):
    row = {**PROFILE_ROW, "display_name": "Alex M"}
    supabase_db.handler = lambda request: httpx.Response(200, json=[row])
    response = users_client.put("/api/v1/users/me", json={"display_name": "  Alex M  "})
    assert response.status_code == 200
    assert response.json()["display_name"] == "Alex M"

    request = supabase_db.requests[0]
    assert request.method == "PATCH"
    assert request.url.params["id"] == f"eq.{USER_ID}"
    assert json.loads(request.content) == {"display_name": "Alex M"}


def test_update_can_remove_onboarding_answers(users_client, supabase_db):
    row = {**PROFILE_ROW, "onboarding_answers": None}
    supabase_db.handler = lambda request: httpx.Response(200, json=[row])
    response = users_client.put("/api/v1/users/me", json={"onboarding_answers": None})
    assert response.status_code == 200
    assert response.json()["onboarding_answers"] is None
    assert json.loads(supabase_db.requests[0].content) == {"onboarding_answers": None}


def test_empty_update_returns_the_profile(users_client, supabase_db):
    supabase_db.handler = lambda request: httpx.Response(200, json=[PROFILE_ROW])
    response = users_client.put("/api/v1/users/me", json={})
    assert response.status_code == 200
    assert supabase_db.requests[0].method == "GET"


def test_update_before_onboarding_is_404(users_client, supabase_db):
    supabase_db.handler = lambda request: httpx.Response(200, json=[])
    response = users_client.put("/api/v1/users/me", json={"display_name": "Alex"})
    assert response.status_code == 404


def test_update_to_taken_username_is_409(users_client, supabase_db):
    supabase_db.handler = lambda request: unique_violation("users_username_key")
    response = users_client.put("/api/v1/users/me", json={"username": "taken_name"})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "username_taken"


@pytest.mark.parametrize(
    "body",
    [
        {"username": None},
        {"display_name": None},
        {"preferred_language": None},
        {"preferred_language": "not a language"},
        {"verified": True},  # cannot be set by the user
        {"privacy_policy_version": "0"},
    ],
)
def test_update_rejects_invalid_requests(users_client, supabase_db, body):
    response = users_client.put("/api/v1/users/me", json=body)
    assert response.status_code == 422
    assert supabase_db.requests == []


# Database failures


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(503, text="Service Unavailable"),
        httpx.Response(429, json={"message": "slow down"}),
        httpx.Response(200, json={"not": "a list"}),
        httpx.Response(200, text="not json"),
    ],
)
def test_database_problems_are_503(users_client, supabase_db, response):
    supabase_db.handler = lambda request: response
    assert users_client.get("/api/v1/users/me").status_code == 503


def test_database_unreachable_is_503(users_client, supabase_db):
    def handler(request):
        raise httpx.ConnectError("unreachable")

    supabase_db.handler = handler
    response = users_client.post("/api/v1/users/me/onboarding", json=ONBOARDING)
    assert response.status_code == 503


def test_database_not_configured_is_503(authenticated_client):
    # The test app has no SUPABASE_SECRET_KEY.
    response = authenticated_client.get("/api/v1/users/me")
    assert response.status_code == 503
    assert response.json() == {"detail": "The database is not configured."}
