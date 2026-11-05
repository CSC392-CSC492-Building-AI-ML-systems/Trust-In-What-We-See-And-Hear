"""Shared, documented error responses."""

from typing import Any, Literal, NoReturn

from fastapi import HTTPException, status
from pydantic import BaseModel

type Responses = dict[int | str, dict[str, Any]]


class NotImplementedDetail(BaseModel):
    code: Literal["not_implemented"] = "not_implemented"
    message: str


class NotImplementedResponse(BaseModel):
    detail: NotImplementedDetail


NOT_IMPLEMENTED_RESPONSES: Responses = {
    status.HTTP_501_NOT_IMPLEMENTED: {
        "model": NotImplementedResponse,
        "description": "Authentication or the requested operation is not implemented.",
    }
}

UNAUTHORIZED_RESPONSES: Responses = {
    status.HTTP_401_UNAUTHORIZED: {"description": "Missing or invalid bearer token."}
}

NOT_FOUND_RESPONSES: Responses = {
    status.HTTP_404_NOT_FOUND: {"description": "Resource not found."}
}


def not_implemented(operation: str) -> NoReturn:
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail=NotImplementedDetail(
            message=f"{operation} is not implemented."
        ).model_dump(),
    )
