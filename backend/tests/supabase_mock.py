"""A routed fake of Supabase's database REST API, for endpoints that make several queries.

Registered as a plugin in conftest.py, so the `db` and `db_client` fixtures are available
to every test module.

AI-assisted code (written with Claude Code), cited per the CSC398 AI policy.
"""

import json
from collections.abc import Callable
from typing import Any
from uuid import UUID

import httpx
import pytest

from app.integrations.supabase import SupabaseDatabase, get_supabase_db

type Reply = httpx.Response | Callable[[httpx.Request], httpx.Response]


class RoutedSupabaseDb:
    """Queue responses per (method, table); each queued response is used once.

    An unexpected request fails the test, so tests also check which queries ran.
    """

    def __init__(self) -> None:
        self.queues: dict[tuple[str, str], list[Reply]] = {}
        self.requests: list[httpx.Request] = []

    def on(self, method: str, table: str, *replies: Reply) -> None:
        self.queues.setdefault((method, table), []).extend(replies)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        table = request.url.path.removeprefix("/rest/v1/")
        queue = self.queues.get((request.method, table))
        if not queue:
            raise AssertionError(f"Unexpected request: {request.method} {request.url}")
        reply = queue.pop(0)
        return reply(request) if callable(reply) else reply

    def sent(self, method: str, table: str) -> list[httpx.Request]:
        return [
            request
            for request in self.requests
            if request.method == method and request.url.path == f"/rest/v1/{table}"
        ]

    def assert_all_used(self) -> None:
        leftover = {key: queue for key, queue in self.queues.items() if queue}
        assert not leftover, f"Queued responses were never requested: {list(leftover)}"


def rows(*items: dict[str, Any], status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=list(items))


def postgres_error(code: str, constraint: str) -> httpx.Response:
    return httpx.Response(
        409,
        json={"code": code, "message": f'violates constraint "{constraint}"'},
    )


def body(request: httpx.Request) -> Any:
    return json.loads(request.content)


def user_row(user_id: UUID, username: str, verified: bool = False) -> dict[str, Any]:
    return {
        "id": str(user_id),
        "username": username,
        "first_name": username.split("_")[0].title(),
        "last_name": username.split("_")[-1].title(),
        "verified_at": "2026-11-01T00:00:00+00:00" if verified else None,
    }


@pytest.fixture
def db():
    fake = RoutedSupabaseDb()
    yield fake
    fake.assert_all_used()


@pytest.fixture
def db_client(application, authenticated_client, db):
    async def override():
        transport = httpx.MockTransport(db)
        async with httpx.AsyncClient(transport=transport) as http_client:
            yield SupabaseDatabase("https://example.supabase.co", "secret", http_client)

    application.dependency_overrides[get_supabase_db] = override
    yield authenticated_client
    application.dependency_overrides.pop(get_supabase_db, None)
