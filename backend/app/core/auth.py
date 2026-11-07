"""Bearer authentication backed by Supabase Auth.
   Code Generated with the help of ClaudeCode.
"""

from typing import Annotated
from uuid import UUID

from fastapi import Depends, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from app.core.errors import auth_service_unavailable, unauthorized
from app.integrations.supabase import (
    SupabaseAuth,
    SupabaseAuthError,
    SupabaseUnavailableError,
    get_supabase_auth,
)

bearer_scheme = HTTPBearer(
    scheme_name="SupabaseBearer",
    description=(
        "Supabase access token from the client's Supabase Auth session "
        "(`session.access_token` in supabase-js)."
    ),
    auto_error=False,
)


class AuthenticatedUser(BaseModel):
    user_id: UUID


async def get_current_user(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Security(bearer_scheme)
    ],
    supabase: Annotated[SupabaseAuth, Depends(get_supabase_auth)],
) -> AuthenticatedUser:
    """Verify the bearer token with Supabase and take the user ID from it.

    Never use a client-supplied acting user ID.
    """
    if credentials is None:
        unauthorized("Missing bearer token.")
    try:
        user_id = await supabase.get_user_id(credentials.credentials)
    except SupabaseAuthError:
        unauthorized()
    except SupabaseUnavailableError:
        auth_service_unavailable()
    return AuthenticatedUser(user_id=user_id)
