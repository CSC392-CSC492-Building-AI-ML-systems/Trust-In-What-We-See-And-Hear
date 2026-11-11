"""API-wide documentation shown on the /docs page."""

API_DESCRIPTION = """
Backend API for Trust In What We See And Hear.

**Status:** in progress. Authentication (Supabase Auth, two-factor required) and
the current user's profile (`/api/v1/users/me`) are implemented. Every other
`/api/v1` operation currently returns `501 Not Implemented` once authenticated.

## Conventions

- **Base path:** feature routes are under `/api/v1`. `/health` is unversioned.
- **Authentication:** email sign-up and sign-in are handled by Supabase Auth
  on the client; this API has no login or registration routes.
  Every `/api/v1` route requires `Authorization: Bearer <token>`, using the
  access token from the client's Supabase session. The acting user is taken
  from the token, never from the request.
- **Two-factor authentication** is required for every user. The token must
  come from a session where the user entered their authenticator code
  (`aal2`); a password-only session (`aal1`) gets `403`.
- **Authentication errors:** `401` means the token is missing, invalid or
  expired; the client should sign the user in again. `403` with
  `detail.code = "mfa_required"` means the token is valid but two-factor
  authentication has not been completed; the client should ask for the
  authenticator code (or set one up) without signing the user out. `503` means
  authentication is temporarily unavailable (for example Supabase is down or
  rate limiting); it says nothing about the token, so the client should retry
  without signing the user out.
- **Pagination:** list routes accept `limit` (1–100, default 20) and `offset`
  (default 0), and return `{items, limit, offset}`.
- **Request bodies:** unknown top-level fields in JSON bodies are rejected with `422`.
- **Asynchronous work:** `202 Accepted` means the request was accepted but the work
  has not finished.

## Main flow

1. Sign up or sign in with Supabase Auth, then complete onboarding (which
   creates your profile) and identity verification.
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
