"""Bearer authentication declaration."""

from typing import Annotated
from uuid import UUID

from fastapi import Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from app.core.errors import not_implemented

bearer_scheme = HTTPBearer(
    scheme_name="SupabaseBearer",
    description=(
        "Supabase JWT access token from `POST /api/v1/auth/login`. "
        "Token validation is not implemented."
    ),
    auto_error=False,
)


class AuthenticatedUser(BaseModel):
    user_id: UUID


def get_current_user(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Security(bearer_scheme)
    ],
) -> AuthenticatedUser:
    """Fail closed, whether a bearer token is supplied or omitted.

    Future implementation must verify the token and derive the user ID from
    its validated subject. Never use a client-supplied acting user ID.
    """
    not_implemented("Authentication")
