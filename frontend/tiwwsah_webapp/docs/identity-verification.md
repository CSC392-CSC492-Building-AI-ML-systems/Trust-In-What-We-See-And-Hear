# Identity verification: frontend guide

Users verify their identity with [Didit](https://didit.me): they scan an ID document, take a selfie
and do a liveness check on Didit's own page. The backend starts the session and reads the result
from Didit. The frontend's job is to send the user to Didit, bring them back, and show the result.

The backend side (and why it works this way) is in
[backend/app/modules/verification/README.md](../../../backend/app/modules/verification/README.md).
The full API reference is at `/docs` on the running backend (tag **Identity verification**).

## What you need to change

1. **Move verification after onboarding.** Today sign-up goes to `/verify-identity` and then to
   `/onboarding` (`components/auth-panel.tsx`). The backend only verifies users who have a profile,
   which onboarding creates, so before onboarding both endpoints answer `404 profile_not_found`.
   The order becomes: sign up → onboarding → verify identity → app.
2. **Let users skip it for now.** Nothing in the app requires a verified user yet, so keep a
   "Skip for now" option and make verification reachable later (for example from settings). When
   the team decides which features need it, those features check `verified`.
3. **Replace the placeholder `IdentityVerification` component.** Its copy says the provider is
   still to be chosen. The provider is Didit; the component needs the states below and the real
   start, return and polling logic.
4. **Make `/verify-identity` also the return page.** Didit sends the user back to the backend's
   `IDENTITY_VERIFICATION_RETURN_URL`, which is `http://localhost:3000/verify-identity` locally. One
   page handles both starting and returning (see [The page](#the-page)).
5. **Check the result on app load.** If the user isn't verified and has a session in progress,
   call `GET /users/me/verification` once when the app loads. This picks up results for users who
   closed the tab before the return page saw them.
6. **Tell the backend team the deployed URL** of `/verify-identity` when the frontend is deployed,
   so they can set `IDENTITY_VERIFICATION_RETURN_URL`.

You don't need any Didit keys or SDK. The frontend never talks to Didit's API; it only links to
the page the backend returns.

## Endpoints

Both need the usual `Authorization: Bearer <access token>` (use `apiFetch` from
`lib/api-fetch.ts`) and act on the signed-in user. Base: `${NEXT_PUBLIC_API_URL}/api/v1`.

### `POST /users/me/verification`: start (or resume)

No body. Returns `202`:

```json
{
  "verification_id": "3f6c…",
  "status": "not_started",
  "url": "https://verify.didit.me/session/…"
}
```

Send the user to `url` in the same tab (`window.location.assign(url)`). Calling `POST` again
before they finish returns the same session and link, so a "Continue" button can just call it
again. Don't store `url`; ask for it each time.

| Error | When | What to show |
|---|---|---|
| `404 profile_not_found` | Onboarding not finished | Send them to `/onboarding` |
| `409 already_verified` | Already verified (possibly just now, on Didit) | The verified state |
| `409 verification_in_review` | A reviewer at Didit is checking their last attempt | The in-review state |
| `503 verification_unavailable` | Didit not configured or unreachable | "Verification is unavailable right now. Try again later." |

Errors use the API's usual shape: `{"detail": {"code": "...", "message": "..."}}`. Branch on
`code`, not `message`.

### `GET /users/me/verification`: current state

Returns `200`:

```json
{
  "verified": false,
  "verification": {
    "id": "3f6c…",
    "status": "in_progress",
    "created_at": "2026-11-24T15:00:00Z",
    "updated_at": "2026-11-24T15:01:10Z"
  }
}
```

- `verified` is the answer to "is this user verified?". Use it, not `status`, to decide.
- `verification` is the latest session, or `null` if they never started.
- While the session isn't finished, each call asks Didit for the latest status, so polling this
  shows the result. If Didit is down it returns the last known status instead of failing.
- `404 profile_not_found` before onboarding.

`GET /users/me` also returns `verified`, from the database only. It can be behind until
`GET /users/me/verification` has been called once after the user finishes.

## Statuses and what to show

| `status` | Meaning | Show | Poll? |
|---|---|---|---|
| (no session) | Never started | Intro and **Verify my identity** | No |
| `not_started`, `in_progress`, `awaiting_user`, `resubmitted` | Not finished, or Didit is still deciding | "Checking your result…", with **Continue verification** (calls `POST` again) | Yes |
| `approved` (`verified: true`) | Passed | "You're verified" and **Continue** | Stop |
| `in_review` | A person at Didit is reviewing it; can take hours | "We're reviewing your ID. We'll update this when it's done." | Stop |
| `declined` | Failed a check | "We couldn't verify your identity." and **Try again** (`POST`) | Stop |
| `expired`, `abandoned` | Never opened, or not finished in time | "Your verification timed out." and **Try again** (`POST`) | Stop |
| `kyc_expired` | The verified document has expired | "Your ID has expired." and **Try again** (`POST`) | Stop |

Don't show why a check was declined. The backend doesn't receive or store those details.

## The page

`/verify-identity` doesn't need to know whether the user is arriving or coming back from Didit. On
load it calls `GET` and renders the state; if the session is unfinished it polls.

```
load /verify-identity
   │
GET /users/me/verification
   ├─ 404 ───────────────────────────► go to /onboarding
   ├─ verified ──────────────────────► "You're verified"
   ├─ no session ────────────────────► intro + [Verify my identity]
   ├─ declined / expired / abandoned ► message + [Try again]
   ├─ in_review ─────────────────────► "We're reviewing it"
   └─ unfinished ────────────────────► "Checking…", poll every 3 s (max ~2 min)
                                          + [Continue verification]

[Verify my identity] / [Try again] / [Continue verification]
   │
POST /users/me/verification
   ├─ 202 ───► window.location.assign(url) ──► Didit ──► back to /verify-identity
   └─ 409 / 503 ► show that state
```

A sketch (adapt to the app's components):

```ts
const API = `${process.env.NEXT_PUBLIC_API_URL}/api/v1/users/me/verification`;
const UNFINISHED = ["not_started", "in_progress", "awaiting_user", "resubmitted"];

async function getState() {
  const res = await apiFetch(API, "GET");
  if (res.status === 404) return { kind: "needs_onboarding" } as const;
  if (!res.ok) throw new Error(`verification ${res.status}`);
  return { kind: "state", ...(await res.json()) } as const;
}

async function start() {
  const res = await apiFetch(API, "POST");
  if (res.status === 202) {
    window.location.assign((await res.json()).url);
    return;
  }
  const code = (await res.json()).detail?.code;
  // already_verified → reload state; verification_in_review → show in review;
  // profile_not_found → /onboarding; verification_unavailable → "try again later"
  return code;
}

// On the page: poll while unfinished, stop on anything else or after ~2 minutes.
async function pollUntilSettled(onUpdate: (s: Awaited<ReturnType<typeof getState>>) => void) {
  for (let i = 0; i < 40; i++) {
    const s = await getState();
    onUpdate(s);
    if (s.kind !== "state" || s.verified || !UNFINISHED.includes(s.verification?.status)) return;
    await new Promise((r) => setTimeout(r, 3000));
  }
}
```

Poll only while the page is open, and stop when it unmounts. If polling times out with the
session still unfinished, keep **Continue verification** visible: the user most likely didn't
finish on Didit.

## Rules

- **Never decide verification on the frontend.** Only the backend sets `verified`, from Didit's
  answer. Coming back to `/verify-identity` doesn't mean the user passed: anyone can open that URL.
  Didit may add query parameters to the return URL; ignore them and call `GET`.
- **Don't put Didit in an iframe or call its API.** Send the user to `url` as a normal page.
- **Don't add Didit keys to the frontend.** There are none for the frontend; the API key is backend
  only.
- **Don't keep personal data.** The frontend never receives ID details, and shouldn't ask the user
  to enter them.

## Testing

Run the backend with the Didit **Sandbox** key (ask the backend team for setup, or see the backend
README). In Sandbox, Didit's page shows a "Sandbox" banner and lets you pick the result:
**Approved**, **In review** or **Declined**. That lets you test every state without a real ID.
Real users on the Live environment don't see the banner or the picker.

Things to check:

- Start → Approved → return → page shows verified; `GET /users/me` shows `verified: true`.
- Start → Declined → return → **Try again** starts a new session.
- Start → In review → return → in-review message; `POST` gives `409 verification_in_review`.
- Start, close Didit's page without finishing, open `/verify-identity` → **Continue verification**
  returns to the same Didit session.
- Before onboarding → redirected to `/onboarding`.
- Backend without Didit configured → "unavailable" message, not a crash.

The backend only learns a result when the user's verification is read (there's no webhook yet), so
the return page polling and the check on app load are what keep the state up to date.

AI-assisted documentation (written with Claude Code), cited per the CSC398 AI policy.
