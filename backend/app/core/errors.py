"""Shared, documented error responses."""

import math
from typing import Any, Literal, NoReturn

from fastapi import HTTPException, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

type Responses = dict[int | str, dict[str, Any]]


class NotImplementedDetail(BaseModel):
    code: Literal["not_implemented"] = "not_implemented"
    message: str


class NotImplementedResponse(BaseModel):
    detail: NotImplementedDetail


NOT_IMPLEMENTED_RESPONSES: Responses = {
    status.HTTP_501_NOT_IMPLEMENTED: {
        "model": NotImplementedResponse,
        "description": "The requested operation is not implemented.",
    }
}

UNAUTHORIZED_RESPONSES: Responses = {
    status.HTTP_401_UNAUTHORIZED: {"description": "Missing or invalid bearer token."}
}

NOT_FOUND_RESPONSES: Responses = {
    status.HTTP_404_NOT_FOUND: {"description": "Resource not found."}
}

SERVICE_UNAVAILABLE_RESPONSES: Responses = {
    status.HTTP_503_SERVICE_UNAVAILABLE: {
        "description": (
            "Supabase (authentication, the database or file storage) is temporarily "
            "unavailable: not configured, unreachable, rate limiting or returned an "
            "unexpected response. Says nothing about the token; retry later."
        )
    }
}


class ErrorDetail(BaseModel):
    code: str = Field(description="Stable, machine-readable error code.")
    message: str


class ErrorResponse(BaseModel):
    detail: ErrorDetail


def error_responses(status_code: int, *codes: tuple[str, str]) -> Responses:
    """Document an error status and the `detail.code` values it can carry.

    Each code is a (code, explanation) pair.
    """
    lines = "\n".join(f"- `{code}`: {explanation}" for code, explanation in codes)
    return {status_code: {"model": ErrorResponse, "description": lines}}


def merge_responses(*groups: Responses) -> Responses:
    """Combine response docs, joining the descriptions of a shared status code."""
    merged: Responses = {}
    for group in groups:
        for status_code, response in group.items():
            if status_code in merged:
                previous = merged[status_code]
                merged[status_code] = {
                    **previous,
                    "description": f"{previous['description']}\n{response['description']}",
                }
            else:
                merged[status_code] = response
    return merged


def api_error(status_code: int, code: str, message: str) -> NoReturn:
    raise HTTPException(
        status_code=status_code,
        detail=ErrorDetail(code=code, message=message).model_dump(),
    )


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
