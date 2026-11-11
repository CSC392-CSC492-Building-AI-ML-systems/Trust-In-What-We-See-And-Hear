import json
from uuid import UUID

import httpx
import pytest

from app.integrations.supabase import SupabaseDatabase, get_supabase_db

USER_ID = UUID(int=1)  # matches the test_user fixture
PROFILE_ROW = {
    "id": str(USER_ID),
    "username": "alex_morgan",
    "first_name": "Alex",
    "last_name": "Morgan",
    "preferred_language": "en",
    "verified_at": None,
    "onboarding_answers": {"age_range": "25-34"},
    "created_at": "2026-11-07T12:00:00+00:00",
}
PROFILE = {
    "user_id": str(USER_ID),
    "username": "alex_morgan",
    "first_name": "Alex",
    "last_name": "Morgan",
    "preferred_language": "en",
    "verified": False,
    "onboarding_answers": {"age_range": "25-34"},
    "created_at": "2026-11-07T12:00:00Z",
}
ONBOARDING = {
    "username": "alex_morgan",
    "first_name": "Alex",
    "last_name": "Morgan",
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
        {"first_name": "   "},
        {"last_name": ""},
        {"last_name": "x" * 51},
        {"user_id": str(UUID(int=2))},  # unknown fields are rejected
        {"answers": {"bio": "x" * 10_000}},  # too large
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


# PATCH /users/me


def test_update_sends_only_the_given_fields(users_client, supabase_db):
    row = {**PROFILE_ROW, "last_name": "Morgan-Lee"}
    supabase_db.handler = lambda request: httpx.Response(200, json=[row])
    response = users_client.patch("/api/v1/users/me", json={"last_name": "  Morgan-Lee  "})
    assert response.status_code == 200
    assert response.json()["last_name"] == "Morgan-Lee"

    request = supabase_db.requests[0]
    assert request.method == "PATCH"
    assert request.url.params["id"] == f"eq.{USER_ID}"
    assert json.loads(request.content) == {"last_name": "Morgan-Lee"}


def test_update_can_remove_onboarding_answers(users_client, supabase_db):
    row = {**PROFILE_ROW, "onboarding_answers": None}
    supabase_db.handler = lambda request: httpx.Response(200, json=[row])
    response = users_client.patch("/api/v1/users/me", json={"onboarding_answers": None})
    assert response.status_code == 200
    assert response.json()["onboarding_answers"] is None
    assert json.loads(supabase_db.requests[0].content) == {"onboarding_answers": None}


def test_empty_update_returns_the_profile(users_client, supabase_db):
    supabase_db.handler = lambda request: httpx.Response(200, json=[PROFILE_ROW])
    response = users_client.patch("/api/v1/users/me", json={})
    assert response.status_code == 200
    assert supabase_db.requests[0].method == "GET"


def test_update_before_onboarding_is_404(users_client, supabase_db):
    supabase_db.handler = lambda request: httpx.Response(200, json=[])
    response = users_client.patch("/api/v1/users/me", json={"first_name": "Alex"})
    assert response.status_code == 404


def test_update_to_taken_username_is_409(users_client, supabase_db):
    supabase_db.handler = lambda request: unique_violation("users_username_key")
    response = users_client.patch("/api/v1/users/me", json={"username": "taken_name"})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "username_taken"


@pytest.mark.parametrize(
    "body",
    [
        {"username": None},
        {"first_name": None},
        {"last_name": None},
        {"preferred_language": None},
        {"preferred_language": "not a language"},
        {"verified": True},  # cannot be set by the user
        {"preferred_language": "en" + "-abcdefgh" * 4},  # longer than 35 characters
        {"privacy_policy_version": "0"},
        {"onboarding_answers": {"bio": "x" * 10_000}},
    ],
)
def test_update_rejects_invalid_requests(users_client, supabase_db, body):
    response = users_client.patch("/api/v1/users/me", json=body)
    assert response.status_code == 422
    assert supabase_db.requests == []


@pytest.mark.parametrize("number", ["NaN", "Infinity", "-Infinity"])
@pytest.mark.parametrize(
    ("method", "url", "field"),
    [
        ("POST", "/api/v1/users/me/onboarding", "answers"),
        ("PATCH", "/api/v1/users/me", "onboarding_answers"),
    ],
)
def test_answers_reject_non_finite_numbers(users_client, supabase_db, method, url, field, number):
    body = ONBOARDING if field == "answers" else {}
    # json.dumps writes NaN/Infinity as bare tokens, which the API's JSON parser accepts.
    content = json.dumps({**body, field: {"score": float(number)}})
    response = users_client.request(
        method, url, content=content, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 422
    assert supabase_db.requests == []


def test_non_finite_number_in_any_field_is_422(users_client, supabase_db):
    # The 422 response echoes the input, so NaN must not break encoding it.
    response = users_client.patch(
        "/api/v1/users/me",
        content='{"username": NaN}',
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422
    assert response.json()["detail"][0]["input"] == "nan"


def test_answers_at_the_size_limit_are_accepted(users_client, supabase_db):
    supabase_db.handler = lambda request: httpx.Response(200, json=[PROFILE_ROW])
    # {"bio":"..."} is 10 bytes plus the value.
    answers = {"bio": "x" * (10_000 - 10)}
    response = users_client.patch("/api/v1/users/me", json={"onboarding_answers": answers})
    assert response.status_code == 200


def test_update_schema_does_not_document_null(application):
    schema = application.openapi()["components"]["schemas"]["UpdateProfileRequest"]
    for name in ("username", "first_name", "last_name", "preferred_language"):
        assert "anyOf" not in schema["properties"][name], name
        assert schema["properties"][name]["type"] == "string"


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


@pytest.mark.parametrize(
    "row",
    [
        {},
        {key: value for key, value in PROFILE_ROW.items() if key != "username"},
        {**PROFILE_ROW, "id": "not-a-uuid"},
        {**PROFILE_ROW, "created_at": None},
    ],
)
@pytest.mark.parametrize(
    ("method", "url", "body"),
    [
        ("GET", "/api/v1/users/me", None),
        ("PATCH", "/api/v1/users/me", {"first_name": "Alex"}),
        ("POST", "/api/v1/users/me/onboarding", ONBOARDING),
    ],
)
def test_malformed_rows_are_503(users_client, supabase_db, row, method, url, body):
    supabase_db.handler = lambda request: httpx.Response(200, json=[row])
    response = users_client.request(method, url, json=body)
    assert response.status_code == 503


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
