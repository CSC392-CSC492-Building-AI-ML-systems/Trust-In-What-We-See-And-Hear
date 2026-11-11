"""Common code and helpers for tests."""

from typing import Annotated
from uuid import UUID

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.core.auth import AuthenticatedUser, get_current_user
from app.core.config import Settings
from app.main import create_app


# A route that only runs authentication, so the auth tests don't depend on real routes.
PROTECTED_URL = "/_test/protected"


def add_protected_test_route(application: FastAPI) -> None:
    async def protected(user: Annotated[AuthenticatedUser, Depends(get_current_user)]):
        return {"user_id": str(user.user_id)}

    application.add_api_route(PROTECTED_URL, protected, methods=["GET"])


@pytest.fixture
def application():
    application = create_app(
        Settings(
            _env_file=None,
            app_name="Test API",
            environment="test",
            supabase_url=None,
            supabase_publishable_key=None,
        )
    )
    add_protected_test_route(application)
    return application


@pytest.fixture
def client(application):
    with TestClient(application) as test_client:
        yield test_client


@pytest.fixture
def test_user():
    return AuthenticatedUser(user_id=UUID(int=1))


@pytest.fixture
def authenticated_client(application, client, test_user):
    application.dependency_overrides[get_current_user] = lambda: test_user
    yield client
    application.dependency_overrides.pop(get_current_user, None)
