"""Identity verification with Didit (https://docs.didit.me).

The backend creates a verification session for a user, and the user completes
it on Didit's hosted page (ID document, liveness and face match). The backend
then asks Didit for the session's status (`session_status`).

Only a session's status is read from Didit. Its decision also holds the
person's name, date of birth, document number and images, which this code
never reads, stores or logs.

AI-assisted code (written with Claude Code), cited per the CSC398 AI policy.
"""

import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx
from fastapi import Request, status

from app.core.errors import api_error

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 10.0

# Every session status Didit documents, in the form stored and returned by
# this API: Didit's "Not Started" is "not_started", "Kyc Expired" is "kyc_expired".
STATUSES = frozenset({
    "not_started", "in_progress", "in_review", "awaiting_user", "resubmitted",
    "approved", "declined", "expired", "abandoned", "kyc_expired",
})


class IdentityVerificationError(Exception):
    """Didit could not be reached, refused the request or answered unexpectedly."""


@dataclass(frozen=True)
class VerificationSession:
    session_id: str
    url: str  # Didit's hosted page, where the user verifies
    status: str  # one of STATUSES


def normalize_status(value: Any) -> str | None:
    """Didit's status as stored here, or None if it is not a known status.

    Didit writes "Not Started" and "Kyc Expired", and one example in its docs
    uses "APPROVED", so case and spaces are not relied on.
    """
    if not isinstance(value, str):
        return None
    normalized = "_".join(value.strip().lower().split())
    return normalized if normalized in STATUSES else None


class DiditClient:
    """Minimal client for Didit's verification API (v3)."""

    def __init__(self, base_url: str, api_key: str, client: httpx.AsyncClient):
        self._base_url = f"{base_url.rstrip('/')}/v3"
        self._api_key = api_key
        self._client = client

    async def create_session(
        self, workflow_id: str, vendor_data: str, callback: str | None
    ) -> VerificationSession:
        """Start a session for one user (`vendor_data` is our user ID).

        Didit returns the user's unfinished session for this workflow, if there
        is one, instead of starting another.
        """
        body: dict[str, Any] = {"workflow_id": workflow_id, "vendor_data": vendor_data}
        if callback:
            body["callback"] = callback
        data = await self._request("POST", "/session/", json=body)
        session_id, url = data.get("session_id"), data.get("url")
        session_status = normalize_status(data.get("status"))
        if not isinstance(session_id, str) or not session_id or not isinstance(url, str):
            raise IdentityVerificationError("Didit returned a session without an ID or a URL.")
        if session_status is None:
            raise IdentityVerificationError("Didit returned a session with an unknown status.")
        return VerificationSession(session_id=session_id, url=url, status=session_status)

    async def session_status(self, session_id: str) -> tuple[str, str | None]:
        """A session's current status and its `vendor_data` (our user ID)."""
        data = await self._request("GET", f"/session/{quote(session_id, safe='')}/decision/")
        session_status = normalize_status(data.get("status"))
        if session_status is None:
            raise IdentityVerificationError("Didit returned an unknown session status.")
        vendor_data = data.get("vendor_data")
        return session_status, vendor_data if isinstance(vendor_data, str) else None

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = await self._client.request(
                method,
                f"{self._base_url}{path}",
                headers={"x-api-key": self._api_key},
                timeout=REQUEST_TIMEOUT_SECONDS,
                **kwargs,
            )
        except httpx.HTTPError as exc:
            raise IdentityVerificationError(f"Didit could not be reached: {exc}") from exc
        if response.is_error:
            # 400 (unknown workflow, no credits), 401/403 (wrong key or scope)
            # and 404 mean our setup is wrong; 429 and 5xx are passing. The
            # body is not logged: it may echo personal data.
            raise IdentityVerificationError(f"Didit answered {response.status_code}.")
        try:
            data = response.json()
        except ValueError as exc:
            raise IdentityVerificationError("Didit returned invalid JSON.") from exc
        if not isinstance(data, dict):
            raise IdentityVerificationError("Didit returned an unexpected response.")
        return data


def _secret(value: Any) -> str:
    # An empty env var gives SecretStr(""), not None.
    return value.get_secret_value() if value is not None else ""


def optional_didit_client(request: Request) -> DiditClient | None:
    """A Didit client, or None when the API key or the workflow ID is not set."""
    settings = request.app.state.settings
    api_key = _secret(settings.didit_api_key)
    if not api_key or not settings.didit_workflow_id:
        return None
    return DiditClient(settings.didit_api_url, api_key, request.app.state.http_client)


def get_didit_client(request: Request) -> DiditClient:
    client = optional_didit_client(request)
    if client is None:
        api_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "verification_unavailable",
            "Identity verification is not configured.",
        )
    return client
