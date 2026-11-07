"""Supabase integration: Auth token verification (database and storage to come).
   Code Generated with the help of ClaudeCode.
"""

import base64
import json
from typing import Any
from uuid import UUID

import httpx
from fastapi import HTTPException, Request, status

REQUEST_TIMEOUT_SECONDS = 10.0

# Statuses Supabase Auth uses to reject a token (invalid, expired, revoked session).
TOKEN_REJECTED_STATUSES = {401, 403}


class SupabaseAuthError(Exception):
    """Supabase Auth rejected the token (401 or 403)."""

    def __init__(self, status_code: int, error_code: str | None, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code
        self.message = message


class SupabaseUnavailableError(Exception):
    """Supabase Auth could not be reached, was rate limiting or misbehaved.

    Retryable: unlike SupabaseAuthError, it says nothing about the token.
    """


class SupabaseAuth:
    """Minimal client for the Supabase Auth REST API.

    Sign-up and sign-in are handled by Supabase on the client, so the backend
    only needs to validate access tokens. Only the publishable key is used.
    """

    def __init__(self, url: str, publishable_key: str, client: httpx.AsyncClient):
        self._base_url = f"{url.rstrip('/')}/auth/v1"
        self._publishable_key = publishable_key
        self._client = client

    async def get_user_id(self, access_token: str) -> UUID:
        """Validate an access token with Supabase and return its subject."""
        body = await self._request("GET", "/user", access_token=access_token)
        user_id = body.get("id")
        # Checked explicitly: UUID() raises different errors for a missing id,
        # a non-string id and a malformed string.
        if not isinstance(user_id, str):
            raise SupabaseUnavailableError("Supabase Auth returned a user without an id.")
        try:
            return UUID(user_id)
        except ValueError as exc:
            raise SupabaseUnavailableError(
                "Supabase Auth returned an invalid user id."
            ) from exc

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
                method,
                f"{self._base_url}{path}",
                headers=headers,
                timeout=REQUEST_TIMEOUT_SECONDS,
                **kwargs,
            )
        except httpx.HTTPError as exc:
            raise SupabaseUnavailableError(str(exc)) from exc

        # The status code alone decides the outcome; the body never changes it.
        if response.status_code in TOKEN_REJECTED_STATUSES:
            body = _json_object_or_empty(response)
            raise SupabaseAuthError(
                status_code=response.status_code,
                # Supabase Auth errors use error_code/msg, with older formats as fallbacks.
                error_code=body.get("error_code") or body.get("error"),
                message=body.get("msg")
                or body.get("error_description")
                or body.get("message")
                or "Supabase Auth request failed.",
            )
        if response.is_error:
            # Rate limits (429), outages (5xx) and other unexpected errors are not
            # the token's fault, so they must not look like a rejected token.
            raise SupabaseUnavailableError(
                f"Supabase Auth returned {response.status_code}."
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise SupabaseUnavailableError("Supabase Auth returned invalid JSON.") from exc
        if not isinstance(body, dict):
            raise SupabaseUnavailableError("Supabase Auth returned an unexpected response.")
        return body


def assurance_level(access_token: str) -> str | None:
    """Return the token's `aal` claim: "aal1" (password only) or "aal2" (with 2FA).

    The signature is not checked here, so only call this after Supabase has
    accepted the same token (e.g. with `SupabaseAuth.get_user_id`).
    Returns None if the claim cannot be read.
    """
    try:
        payload = access_token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (IndexError, ValueError):
        return None
    aal = claims.get("aal") if isinstance(claims, dict) else None
    return aal if isinstance(aal, str) else None


def _json_object_or_empty(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def get_supabase_auth(request: Request) -> SupabaseAuth:
    settings = request.app.state.settings
    # An empty env var (e.g. `SUPABASE_PUBLISHABLE_KEY=`) gives SecretStr(""), not None.
    publishable_key = (
        settings.supabase_publishable_key.get_secret_value()
        if settings.supabase_publishable_key is not None
        else ""
    )
    if not settings.supabase_url or not publishable_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication is not configured.",
        )
    # The shared client is created in the app lifespan (app/main.py).
    return SupabaseAuth(
        settings.supabase_url, publishable_key, request.app.state.http_client
    )
