"""Application creation and router registration."""

from fastapi import FastAPI

from app.api.health import router as health_router
from app.api.router import router as api_router
from app.core.config import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings if settings is not None else get_settings()
    application = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description=(
            "API skeleton. All business operations and "
            "authentication are unimplemented and return HTTP 501."
        ),
    )
    application.state.settings = settings
    application.include_router(health_router)
    application.include_router(api_router)
    return application


app = create_app()
