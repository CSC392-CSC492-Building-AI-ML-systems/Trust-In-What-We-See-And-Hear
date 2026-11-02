# Backend api

<!-- Edited from the default template readme -->

The backend API for the project, built with FastAPI.

## Development

Requires [uv](https://docs.astral.sh/uv/getting-started/installation/) and Python >= 3.14 (which uv can install).

```powershell
uv sync
uv run fastapi dev
```

- API: <http://localhost:8000>
- API docs: <http://localhost:8000/docs>
- Health: <http://localhost:8000/health>
- OpenAPI schema: <http://localhost:8000/openapi.json>

To run tests, `uv run pytest`.

## Current State

Basic skeleton with provisional contracts:
Business routes live under `/api/v1` and return `501 Not Implemented`.
Authentication is also a stub; protected routes accept no credentials yet.

## Structure

```text
main.py             Development entry point
app/main.py         Application factory
app/core/           Configuration, authentication, and shared errors
app/api/            Router registration, health, and shared schemas
app/modules/        Feature routers and request/response schemas
app/integrations/   External service placeholders
tests/              Automated testing with pytest
```

