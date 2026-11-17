# Development image for the backend API, also used for the mock voice pipeline.
# Built by the repository's compose.yaml; see the root README ("Local development").

FROM python:3.14-slim AS base

# ffmpeg decodes uploaded voice samples.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /uvx /bin/

ENV UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Dependencies first, so editing source does not reinstall them.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen


FROM base AS api

COPY . .

EXPOSE 8000
CMD ["fastapi", "dev", "--host", "0.0.0.0", "--port", "8000"]


# The mock voice pipeline only needs FastAPI, so it runs from the backend's environment.
# `voice-pipeline` is an extra build context (compose.yaml points it at voice-pipeline/).
FROM base AS pipeline

COPY --from=voice-pipeline . /pipeline

EXPOSE 8001
CMD ["uvicorn", "--app-dir", "/pipeline", "mock_server:app", "--host", "0.0.0.0", "--port", "8001", "--reload", "--reload-dir", "/pipeline"]
