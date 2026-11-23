# Identity verification (Didit)

Users verify their identity with [Didit](https://docs.didit.me): an ID document, a selfie and a
liveness check, on Didit's own page. The backend starts the session and records the result. The
document, the selfie and the details on them (name, date of birth, document number) stay with
Didit; the backend never reads, stores or logs them. `users.verified_at` only records that a
session passed.

Nothing requires verification yet. Which features need a verified user (for example consenting to
your voice being used) is still to be decided.

## Endpoints

| Endpoint | What it does |
|---|---|
| `POST /api/v1/users/me/verification` | Starts a session, or returns the unfinished one, with Didit's `url` for the user. `202`. |
| `GET /api/v1/users/me/verification` | `{verified, verification}`: whether the user is verified and their latest session. While the session is unsettled, asks Didit first. |

Errors from `POST`: `404 profile_not_found` (onboarding not finished), `409 already_verified`,
`409 verification_in_review` (wait for the review), `503 verification_unavailable` (not
configured, or Didit unreachable).

Statuses are Didit's, in snake case: `not_started`, `in_progress`, `in_review`, `awaiting_user`,
`resubmitted`, `approved`, `declined`, `expired`, `abandoned`, `kyc_expired`.

## How a session goes

1. The frontend calls `POST /users/me/verification` and sends the user to the returned `url`.
2. The user verifies on Didit's page. Didit sends them back to `IDENTITY_VERIFICATION_RETURN_URL`.
3. The frontend polls `GET /users/me/verification`. While the session is unsettled, the backend
   asks Didit for its status, stores it, and sets `users.verified_at` once it is `approved`.

There is no webhook, so the backend only learns a result when the user's verification is read.
That is enough while the user is waiting on the return page. It does mean that if a reviewer at
Didit changes a session that was already `approved` or `declined`, or the document later expires,
the backend does not find out. Add Didit's webhook once the backend has a public URL if that
matters.

## Setting up Didit

1. Sign up at [business.didit.me](https://business.didit.me) and choose **Identity verification**,
   then the **Basic KYC** workflow (ID verification, liveness, face match). Sandbox is free.
2. Create a Sandbox API key with Custom access: Sessions read and write, Decisions read, nothing
   else (no media).
3. Copy into `backend/.env`:
   - `DIDIT_API_KEY`: the key.
   - `DIDIT_WORKFLOW_ID`: the Basic KYC workflow's ID.
   - `IDENTITY_VERIFICATION_RETURN_URL`: the frontend page users come back to, e.g.
     `http://localhost:3000/verify-identity`.

Sandbox and live use different API keys and the same URL. Sandbox sessions are free (500 a day);
live checks use the monthly free credit, then are billed.

## Frontend

- After onboarding (or from settings), call `POST /api/v1/users/me/verification` and open `url`.
- On the return page, poll `GET /api/v1/users/me/verification` every 2 to 3 seconds and show the
  result: `approved` means verified; `in_review` means "we'll let you know" (keep checking now
  and then, e.g. on the next page load); `declined`, `expired` or `abandoned` mean the user can try
  again with `POST`.
- `GET /api/v1/users/me` also returns `verified`.

AI-assisted documentation (written with Claude Code), cited per the CSC398 AI policy.
