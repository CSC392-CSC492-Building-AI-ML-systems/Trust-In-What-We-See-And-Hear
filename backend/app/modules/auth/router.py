from fastapi import APIRouter

from app.core.errors import not_implemented
from app.modules.auth.schemas import (
    LoginRequest,
    RegisterRequest,
    RegistrationResponse,
    TokenResponse,
)

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest) -> TokenResponse:
    """Return an access token to send as `Authorization: Bearer <token>`."""
    not_implemented("Login")


@router.post("/register", response_model=RegistrationResponse, status_code=201)
async def register(payload: RegisterRequest) -> RegistrationResponse:
    """Create an account. No token is returned; log in to get one."""
    not_implemented("Registration")
