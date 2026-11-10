"""Reject request bodies over a size limit before anything reads them.

FastAPI parses (and spools to disk) a whole multipart body before route code
or authentication runs, so the limit has to apply here, to every request,
including chunked ones with no Content-Length.
"""

from fastapi import HTTPException, status
from starlette.types import ASGIApp, Message, Receive, Scope, Send

TOO_LARGE_DETAIL = {"code": "request_too_large", "message": "The request body is too large."}


class _TooLarge(HTTPException):
    """An HTTPException, so FastAPI's body parsing passes it through as a 413."""

    def __init__(self) -> None:
        super().__init__(status.HTTP_413_CONTENT_TOO_LARGE, TOO_LARGE_DETAIL)


class BodySizeLimitMiddleware:
    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        for name, value in scope["headers"]:
            if name == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    declared = 0
                if declared > self.max_bytes:
                    await self._reject(send)
                    return

        received = 0
        response_started = False

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise _TooLarge
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except _TooLarge:
            if not response_started:
                await self._reject(send)

    async def _reject(self, send: Send) -> None:
        body = b'{"detail":{"code":"request_too_large","message":"The request body is too large."}}'
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                    (b"connection", b"close"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
