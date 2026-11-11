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

## API Reference

The generated docs at `/docs` are the API reference. Document endpoints in route docstrings and schema `Field` descriptions, and API-wide conventions in `app/api/openapi.py`.
Mark undecided behavior as **TBD** rather than describing a guess.

## Current State

Skeleton with provisional contracts. Authentication and the current user's profile
(`/api/v1/users/me`) are implemented; other business routes under `/api/v1` return
`501 Not Implemented`.
Authentication is handled by Supabase Auth: the frontend signs users up and in (email and
password) with supabase-js and sends the session's access token as
`Authorization: Bearer <token>`. The backend only verifies that token with Supabase.
Two-factor authentication is required: tokens from password-only sessions get `403`.
Copy `.env.example` to `.env` and set `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY` and
`SUPABASE_SECRET_KEY`; without them, protected routes return `503`.
See [docs/authentication.md](../docs/authentication.md) for Supabase setup and the frontend flow.

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

