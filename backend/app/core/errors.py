"""Shared, documented response for unfinished operations."""

from typing import Literal, NoReturn

from fastapi import HTTPException, status
from pydantic import BaseModel


class NotImplementedDetail(BaseModel):
    code: Literal["not_implemented"] = "not_implemented"
    message: str


class NotImplementedResponse(BaseModel):
    detail: NotImplementedDetail


NOT_IMPLEMENTED_RESPONSES = {
    status.HTTP_501_NOT_IMPLEMENTED: {
        "model": NotImplementedResponse,
        "description": "Authentication or the requested operation is not implemented.",
    }
}


def not_implemented(operation: str) -> NoReturn:
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail=NotImplementedDetail(
            message=f"{operation} is not implemented."
        ).model_dump(),
    )
