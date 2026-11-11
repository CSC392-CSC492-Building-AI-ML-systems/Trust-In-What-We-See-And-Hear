import base64
import json
from uuid import UUID

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.integrations.supabase import SupabaseAuth, get_supabase_auth
from app.main import create_app
from tests.conftest import PROTECTED_URL, add_protected_test_route

USER_ID = UUID(int=42)


def make_token(**claims):
    """A JWT-shaped token. Its signature is fake; Supabase is mocked in these tests."""

    def encode(part):
        return base64.urlsafe_b64encode(json.dumps(part).encode()).rstrip(b"=").decode()

    return f"{encode({'alg': 'ES256'})}.{encode(claims)}.signature"


AAL2_TOKEN = make_token(sub=str(USER_ID), aal="aal2")


class SupabaseMock:
    """Set `handler` in a test to control what the mocked Supabase Auth returns."""

    def __init__(self):
        self.handler = lambda request: httpx.Response(500)


@pytest.fixture
def supabase():
    return SupabaseMock()


@pytest.fixture
def supabase_client(application, client, supabase):
    async def override():
        transport = httpx.MockTransport(lambda request: supabase.handler(request))
        async with httpx.AsyncClient(transport=transport) as http_client:
            yield SupabaseAuth("https://example.supabase.co", "publishable", http_client)

    application.dependency_overrides[get_supabase_auth] = override
    yield client
    application.dependency_overrides.pop(get_supabase_auth, None)


def test_auth_unconfigured_returns_503(client):
    response = client.get(PROTECTED_URL, headers={"Authorization": "Bearer t"})
    assert response.status_code == 503


@pytest.mark.parametrize(
    ("supabase_url", "publishable_key"),
    [("https://example.supabase.co", ""), ("", "publishable")],
)
def test_auth_empty_settings_return_503(supabase_url, publishable_key):
    # Empty env vars, as in .env.example, must count as not configured.
    settings = Settings(
        _env_file=None,
        supabase_url=supabase_url,
        supabase_publishable_key=publishable_key,
    )
    application = create_app(settings)
    add_protected_test_route(application)
    with TestClient(application) as client:
        response = client.get(
            PROTECTED_URL, headers={"Authorization": "Bearer t"}
        )
    assert response.status_code == 503
    # Rejected before any request to Supabase is made.
    assert response.json() == {"detail": "Authentication is not configured."}


def test_protected_route_requires_token(supabase_client):
    response = supabase_client.get(PROTECTED_URL)
    assert response.status_code == 401


def test_protected_route_rejects_invalid_token(supabase_client, supabase):
    supabase.handler = lambda request: httpx.Response(
        403, json={"error_code": "bad_jwt", "msg": "invalid JWT"}
    )
    response = supabase_client.get(
        PROTECTED_URL, headers={"Authorization": "Bearer bad"}
    )
    assert response.status_code == 401


def test_protected_route_accepts_valid_token(supabase_client, supabase):
    def handler(request):
        assert request.url.path == "/auth/v1/user"
        assert request.headers["Authorization"] == f"Bearer {AAL2_TOKEN}"
        return httpx.Response(
            200, json={"id": str(USER_ID), "user_metadata": {"first_name": "Alex"}}
        )

    supabase.handler = handler
    response = supabase_client.get(
        PROTECTED_URL, headers={"Authorization": f"Bearer {AAL2_TOKEN}"}
    )
    assert response.status_code == 200
    assert response.json() == {
        "user_id": str(USER_ID),
        "user_metadata": {"first_name": "Alex"},
    }


@pytest.mark.parametrize("metadata", [None, "not an object", ["a"]])
def test_missing_or_invalid_metadata_becomes_empty(supabase_client, supabase, metadata):
    body = {"id": str(USER_ID)}
    if metadata is not None:
        body["user_metadata"] = metadata
    supabase.handler = lambda request: httpx.Response(200, json=body)
    response = supabase_client.get(
        PROTECTED_URL, headers={"Authorization": f"Bearer {AAL2_TOKEN}"}
    )
    assert response.status_code == 200
    assert response.json()["user_metadata"] == {}


def test_protected_route_when_supabase_is_down(supabase_client, supabase):
    def handler(request):
        raise httpx.ConnectError("unreachable")

    supabase.handler = handler
    response = supabase_client.get(
        PROTECTED_URL, headers={"Authorization": "Bearer good"}
    )
    assert response.status_code == 503


def test_every_api_operation_requires_authentication(application):
    # Catches a route mounted outside `protected_router` by mistake. The OpenAPI
    # schema is used because included routers are not listed in `application.routes`.
    operations = [
        (method.upper(), path, operation)
        for path, item in application.openapi()["paths"].items()
        if path.startswith("/api/v1")
        for method, operation in item.items()
    ]
    assert operations
    for method, path, operation in operations:
        assert {"SupabaseBearer": []} in operation.get("security", []), (
            f"{method} {path} is not protected"
        )


def test_app_shares_one_http_client(application):
    with TestClient(application):
        http_client = application.state.http_client
        assert isinstance(http_client, httpx.AsyncClient)
        assert not http_client.is_closed
    assert http_client.is_closed


@pytest.mark.parametrize(
    ("supabase_status", "body"),
    [
        (200, {"email": "user@example.com"}),  # no id
        (200, {"id": "not-a-uuid"}),
        (200, {"id": 123}),
        (200, {"id": None}),
        (200, ["not", "an", "object"]),
        (200, "just a string"),
    ],
)
def test_protected_route_with_malformed_supabase_response(
    supabase_client, supabase, supabase_status, body
):
    supabase.handler = lambda request: httpx.Response(supabase_status, json=body)
    response = supabase_client.get(
        PROTECTED_URL, headers={"Authorization": "Bearer good"}
    )
    assert response.status_code == 503


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(401, json={"error_code": "no_authorization", "msg": "nope"}),
        httpx.Response(403, json={"error_code": "bad_jwt", "msg": "invalid JWT"}),
        httpx.Response(403, json=["not", "an", "object"]),
        httpx.Response(403, text="<html>Forbidden</html>"),
    ],
)
def test_token_rejection_is_401_whatever_the_body(supabase_client, supabase, response):
    supabase.handler = lambda request: response
    result = supabase_client.get(
        PROTECTED_URL, headers={"Authorization": "Bearer bad"}
    )
    assert result.status_code == 401


@pytest.mark.parametrize(
    "response",
    [
        # A rate limit says nothing about the token; the user must not be signed out.
        httpx.Response(429, json={"error_code": "over_request_rate_limit", "msg": "slow down"}),
        httpx.Response(429, text="Too Many Requests"),
        httpx.Response(400, json={"error_code": "validation_failed", "msg": "bad"}),
        httpx.Response(404, json=["not", "an", "object"]),
        httpx.Response(502, text="Bad Gateway"),
    ],
)
def test_non_token_errors_are_503(supabase_client, supabase, response):
    supabase.handler = lambda request: response
    result = supabase_client.get(
        PROTECTED_URL, headers={"Authorization": "Bearer good"}
    )
    assert result.status_code == 503


@pytest.mark.parametrize(
    "token",
    [
        make_token(sub=str(USER_ID), aal="aal1"),  # password only
        make_token(sub=str(USER_ID)),  # no aal claim
        "not-a-jwt",
        "a.!!!.c",  # payload is not base64
        f"a.{base64.urlsafe_b64encode(b'[1, 2]').decode()}.c",  # payload is not an object
    ],
)
def test_valid_token_without_mfa_is_403(supabase_client, supabase, token):
    supabase.handler = lambda request: httpx.Response(200, json={"id": str(USER_ID)})
    response = supabase_client.get(
        PROTECTED_URL, headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "mfa_required"


def test_rejected_token_is_401_even_if_it_claims_aal2(supabase_client, supabase):
    # The aal claim is only trusted once Supabase has accepted the token.
    supabase.handler = lambda request: httpx.Response(
        403, json={"error_code": "bad_jwt", "msg": "invalid JWT"}
    )
    response = supabase_client.get(
        PROTECTED_URL, headers={"Authorization": f"Bearer {AAL2_TOKEN}"}
    )
    assert response.status_code == 401
