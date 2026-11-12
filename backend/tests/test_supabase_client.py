"""Tests for the Supabase database client's query helpers.

AI-assisted code (written with Claude Code), cited per the CSC398 AI policy.
"""

import asyncio

import httpx
import pytest

from app.integrations.supabase import SupabaseDatabase


def test_delete_refuses_to_run_without_filters():
    # PostgREST would delete every row in the table.
    requests = []

    async def run():
        transport = httpx.MockTransport(lambda request: requests.append(request))
        async with httpx.AsyncClient(transport=transport) as http_client:
            database = SupabaseDatabase("https://example.supabase.co", "secret", http_client)
            await database.delete("friendships", {}, "id")

    with pytest.raises(ValueError):
        asyncio.run(run())
    assert requests == []
