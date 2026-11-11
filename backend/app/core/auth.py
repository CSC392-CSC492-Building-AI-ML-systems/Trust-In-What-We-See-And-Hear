"""Bearer authentication backed by Supabase Auth.
   Code Generated with the help of ClaudeCode.
"""

from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

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
    # Supabase `user_metadata`. The user can change it, so validate before use.
    user_metadata: dict[str, Any] = Field(default_factory=dict)


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
        supabase_user = await supabase.get_user(credentials.credentials)
    except SupabaseAuthError:
        unauthorized()
    except SupabaseUnavailableError:
        auth_service_unavailable()
    return AuthenticatedUser(
        user_id=supabase_user.id, user_metadata=supabase_user.user_metadata
    )
