"""Shared, documented error responses."""

import math
from typing import Any, Literal, NoReturn

from fastapi import HTTPException, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel

type Responses = dict[int | str, dict[str, Any]]


class NotImplementedDetail(BaseModel):
    code: Literal["not_implemented"] = "not_implemented"
    message: str


class NotImplementedResponse(BaseModel):
    detail: NotImplementedDetail


class MfaRequiredDetail(BaseModel):
    code: Literal["mfa_required"] = "mfa_required"
    message: str


class MfaRequiredResponse(BaseModel):
    detail: MfaRequiredDetail


NOT_IMPLEMENTED_RESPONSES: Responses = {
    status.HTTP_501_NOT_IMPLEMENTED: {
        "model": NotImplementedResponse,
        "description": "The requested operation is not implemented.",
    }
}

UNAUTHORIZED_RESPONSES: Responses = {
    status.HTTP_401_UNAUTHORIZED: {"description": "Missing or invalid bearer token."}
}

MFA_REQUIRED_RESPONSES: Responses = {
    status.HTTP_403_FORBIDDEN: {
        "model": MfaRequiredResponse,
        "description": (
            "The token is valid, but the user has not completed two-factor "
            "authentication in this session (`aal1`). The client should ask for "
            "the authenticator code, or set up an authenticator, and retry with "
            "the new token. Do not sign the user out."
        ),
    }
}

NOT_FOUND_RESPONSES: Responses = {
    status.HTTP_404_NOT_FOUND: {"description": "Resource not found."}
}

SERVICE_UNAVAILABLE_RESPONSES: Responses = {
    status.HTTP_503_SERVICE_UNAVAILABLE: {
        "description": (
            "Supabase (authentication or the database) is temporarily unavailable: "
            "not configured, unreachable, rate limiting or returned an unexpected "
            "response. Says nothing about the token; retry later."
        )
    }
}


def not_implemented(operation: str) -> NoReturn:
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail=NotImplementedDetail(
            message=f"{operation} is not implemented."
        ).model_dump(),
    )


def auth_service_unavailable() -> NoReturn:
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Authentication service is unavailable.",
    )


def mfa_required() -> NoReturn:
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=MfaRequiredDetail(
            message="Two-factor authentication is required."
        ).model_dump(),
    )


def database_unavailable() -> NoReturn:
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="The database is unavailable.",
    )


def unauthorized(message: str = "Invalid or expired bearer token.") -> NoReturn:
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=message,
        headers={"WWW-Authenticate": "Bearer"},
    )


async def request_validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """FastAPI's default 422 response, safe for any input.

    The default response echoes the invalid input, and a request body may contain
    `NaN` or `Infinity`, which cannot be encoded as JSON. Without this they give `500`.
    """
    errors = _replace_non_finite(jsonable_encoder(exc.errors()))
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, content={"detail": errors}
    )


def _replace_non_finite(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, dict):
        return {key: _replace_non_finite(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace_non_finite(item) for item in value]
    return value
