"""API-wide documentation shown on the /docs page."""

API_DESCRIPTION = """
Backend API for Trust In What We See And Hear.

**Status:** skeleton. Every `/api/v1` operation, including authentication,
currently returns `501 Not Implemented`.

## Conventions

- **Base path:** feature routes are under `/api/v1`. `/health` is unversioned.
- **Authentication:** every `/api/v1` route except login and registration requires
  `Authorization: Bearer <token>`, using the Supabase JWT returned by login.
  The acting user is taken from the token, never from the request.
- **Pagination:** list routes accept `limit` (1–100, default 20) and `offset`
  (default 0), and return `{items, limit, offset}`.
- **Request bodies:** unknown top-level fields in JSON bodies are rejected with `422`.
- **Asynchronous work:** `202 Accepted` means the request was accepted but the work
  has not finished.

## Main flow

1. Register, log in, complete onboarding and identity verification.
2. Upload a voice sample to train your voice model.
3. Add friends.
4. Request consent from a friend to use their profile and voice. Once they
   accept, the consent can be used for a limited time.
5. Start a call with that consent and a scenario, then review the result in
   call history.
"""

OPENAPI_TAGS = [
    {
        "name": "Health",
        "description": "Process liveness. Does not check external services.",
    },
    {
        "name": "Authentication",
        "description": "Registration and login. No token required.",
    },
    {
        "name": "Users",
        "description": "The current user's profile and onboarding.",
    },
    {
        "name": "Identity verification",
        "description": "Verification through an external provider (Persona or similar).",
    },
    {
        "name": "Friends",
        "description": "Friend requests and friendships.",
    },
    {
        "name": "Consents",
        "description": (
            "Permission from a friend to use their profile and voice in a "
            "scam call to the requester. Accepted consents expire after a "
            "short period set by the server."
        ),
    },
    {
        "name": "Calls",
        "description": "Simulated scam calls, each based on a consent and a scenario.",
    },
    {
        "name": "Voice training",
        "description": "Training a user's voice model from an MP3 sample.",
    },
    {
        "name": "Scenarios",
        "description": (
            "Scam scenarios that calls are based on.\n\n"
            "**TBD:** the content format, whether scenarios are shared "
            "between users, and who can edit or delete them."
        ),
    },
]
