"""Profile fields that other users may see.

Friends and consents show the other user through these fields only. Never add
onboarding answers or anything else private here.
"""

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

PUBLIC_USER_COLUMNS = "id,username,first_name,last_name,verified_at"


class PublicUser(BaseModel):
    user_id: UUID
    username: str
    first_name: str
    last_name: str
    verified: bool = Field(description="Whether identity verification has passed.")


def to_public_user(row: dict[str, Any]) -> PublicUser:
    return PublicUser(
        user_id=row["id"],
        username=row["username"],
        first_name=row["first_name"],
        last_name=row["last_name"],
        verified=row["verified_at"] is not None,
    )
