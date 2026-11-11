"""Supabase integration: Auth token verification and database access (storage to come).
   Code Generated with the help of ClaudeCode.
"""

from dataclasses import dataclass
from typing import Any
from uuid import UUID

import httpx
from fastapi import HTTPException, Request, status
from pydantic import SecretStr

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
    """Supabase could not be reached, was rate limiting or misbehaved.

    Retryable: unlike SupabaseAuthError, it says nothing about the token.
    """


class SupabaseConflictError(Exception):
    """A database write broke a unique constraint (Postgres error 23505)."""

    def __init__(self, constraint: str | None, message: str):
        super().__init__(message)
        self.constraint = constraint


class SupabaseDatabaseError(Exception):
    """The database rejected a request for a reason that indicates a bug."""


@dataclass(frozen=True)
class SupabaseUser:
    id: UUID
    # Set by the client at sign-up (`options.data`) and editable by the user, so
    # treat it as unverified input.
    user_metadata: dict[str, Any]


class SupabaseAuth:
    """Minimal client for the Supabase Auth REST API.

    Sign-up and sign-in are handled by Supabase on the client, so the backend
    only needs to validate access tokens. Only the publishable key is used.
    """

    def __init__(self, url: str, publishable_key: str, client: httpx.AsyncClient):
        self._base_url = f"{url.rstrip('/')}/auth/v1"
        self._publishable_key = publishable_key
        self._client = client

    async def get_user(self, access_token: str) -> SupabaseUser:
        """Validate an access token with Supabase and return the user it belongs to."""
        body = await self._request("GET", "/user", access_token=access_token)
        user_id = body.get("id")
        # Checked explicitly: UUID() raises different errors for a missing id,
        # a non-string id and a malformed string.
        if not isinstance(user_id, str):
            raise SupabaseUnavailableError("Supabase Auth returned a user without an id.")
        try:
            parsed_id = UUID(user_id)
        except ValueError as exc:
            raise SupabaseUnavailableError(
                "Supabase Auth returned an invalid user id."
            ) from exc
        metadata = body.get("user_metadata")
        return SupabaseUser(
            id=parsed_id, user_metadata=metadata if isinstance(metadata, dict) else {}
        )

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


def _json_object_or_empty(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


class SupabaseDatabase:
    """Minimal client for Supabase's database REST API (PostgREST).

    Uses the secret key, which bypasses Row Level Security, so every query must
    filter by the authenticated user itself.
    """

    def __init__(self, url: str, secret_key: str, client: httpx.AsyncClient):
        self._base_url = f"{url.rstrip('/')}/rest/v1"
        self._secret_key = secret_key
        self._client = client

    async def select(
        self, table: str, filters: dict[str, str], columns: str
    ) -> list[dict[str, Any]]:
        """Return the rows matching `filters`, e.g. {"id": "eq.<uuid>"}."""
        return await self._request("GET", table, params={**filters, "select": columns})

    async def insert(
        self, table: str, row: dict[str, Any], columns: str
    ) -> dict[str, Any]:
        rows = await self._request(
            "POST",
            table,
            params={"select": columns},
            json=row,
            headers={"Prefer": "return=representation"},
        )
        if len(rows) != 1:
            raise SupabaseDatabaseError(f"Insert into {table} returned {len(rows)} rows.")
        return rows[0]

    async def update(
        self, table: str, filters: dict[str, str], values: dict[str, Any], columns: str
    ) -> list[dict[str, Any]]:
        """Update the rows matching `filters` and return them (empty if none matched)."""
        return await self._request(
            "PATCH",
            table,
            params={**filters, "select": columns},
            json=values,
            headers={"Prefer": "return=representation"},
        )

    async def _request(
        self,
        method: str,
        table: str,
        *,
        headers: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        try:
            response = await self._client.request(
                method,
                f"{self._base_url}/{table}",
                headers={
                    "apikey": self._secret_key,
                    "Authorization": f"Bearer {self._secret_key}",
                    **(headers or {}),
                },
                timeout=REQUEST_TIMEOUT_SECONDS,
                **kwargs,
            )
        except httpx.HTTPError as exc:
            raise SupabaseUnavailableError(str(exc)) from exc

        if response.status_code == 429 or response.status_code >= 500:
            raise SupabaseUnavailableError(f"Supabase returned {response.status_code}.")
        if response.is_error:
            error = _json_object_or_empty(response)
            message = error.get("message") or f"Supabase returned {response.status_code}."
            if error.get("code") == "23505":
                raise SupabaseConflictError(_constraint_name(message), message)
            raise SupabaseDatabaseError(message)
        try:
            body = response.json()
        except ValueError as exc:
            raise SupabaseUnavailableError("Supabase returned invalid JSON.") from exc
        if not isinstance(body, list) or not all(isinstance(row, dict) for row in body):
            raise SupabaseUnavailableError("Supabase returned an unexpected response.")
        return body


def _constraint_name(message: str) -> str | None:
    # e.g. 'duplicate key value violates unique constraint "users_username_key"'
    parts = message.split('"')
    return parts[1] if len(parts) >= 3 else None


def _secret(value: SecretStr | None) -> str:
    # An empty env var (e.g. `SUPABASE_PUBLISHABLE_KEY=`) gives SecretStr(""), not None.
    return value.get_secret_value() if value is not None else ""


def get_supabase_auth(request: Request) -> SupabaseAuth:
    settings = request.app.state.settings
    publishable_key = _secret(settings.supabase_publishable_key)
    if not settings.supabase_url or not publishable_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication is not configured.",
        )
    # The shared client is created in the app lifespan (app/main.py).
    return SupabaseAuth(
        settings.supabase_url, publishable_key, request.app.state.http_client
    )


def get_supabase_db(request: Request) -> SupabaseDatabase:
    settings = request.app.state.settings
    secret_key = _secret(settings.supabase_secret_key)
    if not settings.supabase_url or not secret_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The database is not configured.",
        )
    return SupabaseDatabase(settings.supabase_url, secret_key, request.app.state.http_client)
