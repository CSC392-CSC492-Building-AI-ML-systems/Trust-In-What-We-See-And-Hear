from uuid import UUID

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.integrations.supabase import SupabaseAuth, get_supabase_auth
from app.main import create_app

USER_ID = UUID(int=42)


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
    response = client.get("/api/v1/users/me", headers={"Authorization": "Bearer t"})
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
    with TestClient(create_app(settings)) as client:
        response = client.get(
            "/api/v1/users/me", headers={"Authorization": "Bearer t"}
        )
    assert response.status_code == 503
    # Rejected before any request to Supabase is made.
    assert response.json() == {"detail": "Authentication is not configured."}


def test_protected_route_requires_token(supabase_client, supabase):
    response = supabase_client.get("/api/v1/users/me")
    assert response.status_code == 401


def test_protected_route_rejects_invalid_token(supabase_client, supabase):
    handler = lambda request: httpx.Response(
        403, json={"error_code": "bad_jwt", "msg": "invalid JWT"}
    )
    supabase.handler = handler
    response = supabase_client.get(
        "/api/v1/users/me", headers={"Authorization": "Bearer bad"}
    )
    assert response.status_code == 401


def test_protected_route_accepts_valid_token(supabase_client, supabase):
    def handler(request):
        assert request.url.path == "/auth/v1/user"
        assert request.headers["Authorization"] == "Bearer good"
        return httpx.Response(200, json={"id": str(USER_ID)})

    supabase.handler = handler
    response = supabase_client.get(
        "/api/v1/users/me", headers={"Authorization": "Bearer good"}
    )
    # Authentication passed; the profile route itself is still a stub.
    assert response.status_code == 501


def test_protected_route_when_supabase_is_down(supabase_client, supabase):
    def handler(request):
        raise httpx.ConnectError("unreachable")

    supabase.handler = handler
    response = supabase_client.get(
        "/api/v1/users/me", headers={"Authorization": "Bearer good"}
    )
    assert response.status_code == 503
