"""Application creation and router registration."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.health import router as health_router
from app.api.openapi import API_DESCRIPTION, OPENAPI_TAGS
from app.api.router import router as api_router
from app.core.body_limit import BodySizeLimitMiddleware
from app.core.config import Settings, get_settings
from app.core.errors import request_validation_error_handler
from app.integrations.supabase import SupabaseUnavailableError
from app.modules.voices.audio import AudioDecoder


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    """Share one HTTP client (and connection pool) across all requests."""
    async with httpx.AsyncClient() as client:
        application.state.http_client = client
        yield


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings if settings is not None else get_settings()
    application = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description=API_DESCRIPTION,
        openapi_tags=OPENAPI_TAGS,
        lifespan=lifespan,
    )
    application.state.settings = settings
    application.state.audio_decoder = AudioDecoder()
    application.add_exception_handler(
        RequestValidationError, request_validation_error_handler
    )

    @application.exception_handler(SupabaseUnavailableError)
    async def supabase_unavailable(request: Request, exc: SupabaseUnavailableError) -> JSONResponse:
        # Authentication catches its own outages first, so this is the database.
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"detail": "The database is unavailable."},
        )

    # Room for the largest voice sample plus the multipart form around it.
    application.add_middleware(
        BodySizeLimitMiddleware, max_bytes=settings.voice_sample_max_bytes + 64 * 1024
    )
    application.include_router(health_router)
    application.include_router(api_router)
    return application


app = create_app()
