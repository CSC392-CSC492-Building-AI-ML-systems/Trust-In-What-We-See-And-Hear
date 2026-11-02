from typing import Literal
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, SecretStr

from app.api.schemas import RequestModel


class LoginRequest(RequestModel):
    email: EmailStr
    password: SecretStr = Field(min_length=1)


class RegisterRequest(LoginRequest):
    pass


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"


class RegistrationResponse(BaseModel):
    user_id: UUID
