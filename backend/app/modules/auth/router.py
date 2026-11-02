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
    not_implemented("Login")


@router.post("/register", response_model=RegistrationResponse, status_code=201)
async def register(payload: RegisterRequest) -> RegistrationResponse:
    not_implemented("Registration")
