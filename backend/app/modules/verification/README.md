# Identity verification (Didit)

Users verify their identity with [Didit](https://docs.didit.me): an ID document, a selfie and a
liveness check, on Didit's own page. The backend starts the session and records the result. The
document, the selfie and the details on them (name, date of birth, document number) stay with
Didit; the backend never reads, stores or logs them. `users.verified_at` only records that a
session passed.

Nothing requires verification yet. Which features need a verified user (for example consenting to
your voice being used) is still to be decided.

The frontend's side (pages, polling, error handling) is in
[frontend/tiwwsah_webapp/docs/identity-verification.md](../../../../frontend/tiwwsah_webapp/docs/identity-verification.md).

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

- **Settled** (`approved`, `declined`, `expired`, `abandoned`, `kyc_expired`): Didit never
  changes these on its own, so the backend stops asking about them. Only a reviewer at Didit can,
  which the backend does not see without a webhook (below).
- **Unsettled** (all others): the backend asks Didit for the status whenever it reads the session.
- `kyc_expired` is listed because it is one of Didit's statuses, but the backend cannot see it
  yet: an approved session is settled, so it is never checked again. See the webhook plan.

## How a session goes

1. The frontend calls `POST /users/me/verification` and sends the user to the returned `url`.
2. The user verifies on Didit's page. Didit sends them back to `IDENTITY_VERIFICATION_RETURN_URL`.
3. The frontend polls `GET /users/me/verification`. While the session is unsettled, the backend
   asks Didit for its status, stores it, and sets `users.verified_at` once it is `approved`.

Details that keep this correct:

- **Didit reuses unfinished sessions.** For a User Verification workflow, "calling create again
  with the same pair [workflow_id, vendor_data] returns the existing session" while it is
  `Not Started`, `In Progress`, `Resubmitted` or `Awaiting User`
  ([Create Session](https://docs.didit.me/sessions-api/create-session)). So repeated `POST`s
  return the same link and do not start new sessions. Didit only charges when a check "finishes
  with a result" ([Pricing](https://docs.didit.me/getting-started/pricing)).
- **`POST` checks an unsettled session with Didit before starting.** Didit never reuses a session
  that is `In Review` or `Approved`, so if the stored status were behind (the user finished but
  nothing called `GET`), `POST` would otherwise start a second session and lose track of the first.
  If that session was approved, `POST` answers `409 already_verified`.
- **`verified_at` is set before the session is saved as `approved`.** If the second write fails,
  the session is still unsettled, so the next read asks Didit again and finishes the job. The
  `verified_at` write only matches a user whose `verified_at` is null, so it is safe to repeat,
  and an earlier verification's time is kept.
- **The session must belong to the user.** Each session is created with `vendor_data` set to the
  user's ID. A status read from Didit for a different `vendor_data` is ignored and logged.

## Decision: polling, not a webhook (for now)

The backend learns a session's result by asking Didit when the session is read, and the frontend
polls `GET /users/me/verification` after the user returns from Didit. We chose this over Didit's
webhook because:

- **A webhook needs a public URL.** Didit has to reach the backend over the internet. The backend
  is not deployed yet, and running it locally would need a tunnel (ngrok, cloudflared) for every
  developer.
- **Polling is cheap here.** A user verifies once. Didit usually decides within seconds, so the
  return page makes a few requests and stops. A `GET` that finds nothing new writes nothing.
- **It is correct, only sometimes late.** The result is always read from Didit by the backend, so
  the client cannot fake it. If Didit is down, `GET` returns the stored status.

What it costs:

- **Late results.** If the user leaves before the return page sees the result, the database is
  behind until something reads the verification again (the frontend checks on app load; `POST`
  also checks). Until then `GET /users/me` shows `verified: false`.
- **Changes after a decision are missed.** A reviewer at Didit can change a decided session (for
  example approve an `abandoned` one or decline an `approved` one), and an approved document can
  reach its expiry date (`kyc_expired`). The backend never asks about settled sessions, so it does
  not see these.

## Plan: Didit's webhook, once the backend has a public URL

Add it when the backend is deployed, or sooner if missed changes start to matter. It replaces
waiting for a read; the polling path stays as a fallback.

1. **Endpoint.** `POST /api/v1/webhooks/didit`, with no user authentication (Didit is the caller).
   Register it in the Didit console (**Webhooks**, one per environment) for session status
   changes, and put its secret in a new `DIDIT_WEBHOOK_SECRET` setting.
2. **Verify every request.** Check Didit's `X-Signature-V2` header (HMAC-SHA256 with the webhook
   secret, over the body as Didit's webhook docs describe; compare in constant time) and reject
   stale deliveries, before acting on anything. Reject unsigned or badly signed requests with
   `401`.
3. **Apply the status.** Find the row by `provider_session_id`, check `vendor_data` matches its
   user, and call the same `apply_status` as `GET`. Ignore unknown sessions (return `200` so Didit
   does not retry forever). Answer quickly; Didit retries on errors, and `apply_status` is safe to
   repeat.
4. **Handle changes after a decision.** For a session that becomes `declined` or `kyc_expired`
   after being `approved`, clear `users.verified_at` if that session was what verified the user.
   This is the case polling cannot cover.
5. **Frontend.** Polling can stay as it is. Optionally, the frontend can instead subscribe to its
   own `users` row with Supabase Realtime and update when `verified_at` is set, which needs
   Realtime turned on for that table.
6. **Tests.** Valid, invalid and missing signatures; an unknown session; a session for another
   user; a repeated delivery; approval then a later decline.

## Setting up Didit

1. Sign up at [business.didit.me](https://business.didit.me) and choose **Identity verification**,
   then the **Basic KYC** workflow (ID verification, liveness, face match). Sandbox is free.
2. Create an API key with Custom access: Sessions read and write, Decisions read, nothing else
   (no media). Leave **Only approved sessions** off: the backend must see unfinished, in-review and
   declined sessions. Optionally limit it to the Basic KYC workflow.
3. Copy into `backend/.env`:
   - `DIDIT_API_KEY`: the key.
   - `DIDIT_WORKFLOW_ID`: the Basic KYC workflow's ID.
   - `IDENTITY_VERIFICATION_RETURN_URL`: the frontend page users come back to, e.g.
     `http://localhost:3000/verify-identity`.

### Sandbox and live

Live and Sandbox are separate environments of the same Didit application (switch between them in
the console's sidebar). Each has its own API keys (`sk_test_…` for Sandbox, `sk_live_…` for Live)
and its own workflows, so the key and the workflow ID must come from the same environment, or
Didit answers `403`. The API URL is the same; nothing in the code changes.

- **Sandbox:** no real checks run. Didit's page lets you pick the result (Approved, In review or
  Declined), which is useful for testing and demos. Sessions are never billed (500 a day).
- **Live:** real checks on a real ID and face. About $0.33 per completed check, paid from the free
  plan's monthly credit, then by card.

Use Sandbox for development and Live only on the deployed backend.

AI-assisted documentation (written with Claude Code), cited per the CSC398 AI policy.
