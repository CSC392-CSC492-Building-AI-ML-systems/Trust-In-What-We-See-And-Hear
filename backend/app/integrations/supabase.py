"""Supabase database and private storage integration.
   Code Generated with the help of ClaudeCode.
"""

from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

import httpx
from fastapi import HTTPException, Request, status

REQUEST_TIMEOUT_SECONDS = 10.0


class SupabaseAuthError(Exception):
    """Supabase Auth rejected a request, e.g. an invalid or expired token."""

    def __init__(self, status_code: int, error_code: str | None, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code
        self.message = message


class SupabaseUnavailableError(Exception):
    """Supabase Auth could not be reached or returned a server error."""


class SupabaseAuth:
    """Minimal client for the Supabase Auth REST API.

    Sign-up and sign-in are handled by Supabase on the client, so the backend
    only needs to validate access tokens. Only the publishable
    key is used.
    """

    def __init__(self, url: str, publishable_key: str, client: httpx.AsyncClient):
        self._base_url = f"{url.rstrip('/')}/auth/v1"
        self._publishable_key = publishable_key
        self._client = client

    async def get_user_id(self, access_token: str) -> UUID:
        """Validate an access token with Supabase and return its subject."""
        body = await self._request("GET", "/user", access_token=access_token)
        return UUID(body["id"])

    async def _request(
        self,
        method: str,
        path: str,
        *,
        access_token: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        headers = {"apikey": self._publishable_key}
        if access_token is not None:
            headers["Authorization"] = f"Bearer {access_token}"
        try:
            response = await self._client.request(
                method, f"{self._base_url}{path}", headers=headers, **kwargs
            )
        except httpx.HTTPError as exc:
            raise SupabaseUnavailableError(str(exc)) from exc

        if response.status_code >= 500:
            raise SupabaseUnavailableError(
                f"Supabase Auth returned {response.status_code}."
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise SupabaseUnavailableError("Supabase Auth returned invalid JSON.") from exc
        if response.is_error:
            raise SupabaseAuthError(
                status_code=response.status_code,
                # Supabase Auth errors use error_code/msg, with older formats as fallbacks.
                error_code=body.get("error_code") or body.get("error"),
                message=body.get("msg")
                or body.get("error_description")
                or body.get("message")
                or "Supabase Auth request failed.",
            )
        return body


async def get_supabase_auth(request: Request) -> AsyncIterator[SupabaseAuth]:
    settings = request.app.state.settings
    if not settings.supabase_url or settings.supabase_publishable_key is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication is not configured.",
        )
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
        yield SupabaseAuth(
            settings.supabase_url,
            settings.supabase_publishable_key.get_secret_value(),
            client,
        )
