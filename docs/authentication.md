# Authentication

Authentication is handled by **Supabase Auth**. The backend has no login or registration endpoints.

- The **frontend** signs users up and in directly with Supabase using **email and password** (the
  only sign-in method for now). Supabase stores the session in cookies.
- For every backend request, the frontend sends the session's access token:
  `Authorization: Bearer <access_token>`.
- The **backend** verifies the token with Supabase and takes the user ID from it. A missing, invalid
  or expired token gets `401`.
- The app profile (`public.users`) is created by the backend when the user finishes onboarding. A
  signed-in user without a profile gets `404` from `GET /api/v1/users/me` and should be sent to
  onboarding. See [database-schema.md](../supabase/database-schema.md#users) for why the table exists.
- **Multi-factor authentication (MFA)** with an authenticator app (TOTP) must be supported. TOTP is
  already enabled in the Supabase project; the app needs a setup screen, a code prompt at login
  (section 2.9) and a backend check (section 3). Whether MFA is optional or required for every
  user is **TBD**.

```text
Browser ──sign up / sign in──▶ Supabase Auth
   │                                        │
   │◀──────── session (cookies) ────────────┘
   │
   └──Authorization: Bearer <access_token>──▶ FastAPI ──verify token──▶ Supabase Auth
```

Contents:

1. [Supabase project setup](#1-supabase-project-setup)
2. [Frontend implementation](#2-frontend-implementation)
3. [Backend contract](#3-backend-contract)
4. [Testing the flow locally](#4-testing-the-flow-locally)

---

## 1. Supabase project setup

Done once per Supabase project, in the [Supabase dashboard](https://supabase.com/dashboard).

### 1.1 Keys

In **Project Settings → API Keys** (and **Data API** for the URL), copy:

| Value | Used by | Where it goes |
|---|---|---|
| Project URL (`https://<project-ref>.supabase.co`) | Frontend and backend | `NEXT_PUBLIC_SUPABASE_URL`, `SUPABASE_URL` |
| Publishable key (`sb_publishable_…`, or the legacy `anon` key) | Frontend and backend | `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY`, `SUPABASE_PUBLISHABLE_KEY` |
| Secret key (`sb_secret_…`, or the legacy `service_role` key) | Backend only, once it writes to the database | Not used yet. **Never** put it in the frontend or commit it. |

The publishable key is safe in the browser. Row Level Security protects the data.

### 1.2 Email sign-in

**Authentication → Sign In / Providers**:

- Keep **Email** enabled. Leave every other provider disabled; email is the only sign-in method
  for now.
- **Confirm email**: turn it on for real users. Turning it off during local development is simpler,
  because sign-up then signs the user in immediately.
- Set the minimum password length to **8** to match the sign-up form.

### 1.3 Redirect URLs

**Authentication → URL Configuration**:

- **Site URL**: `http://localhost:3000` for now. Change it to the deployed frontend URL when there
  is one.
- **Redirect URLs**: add `http://localhost:3000/**`, and the deployed URL with `/**` later.
  Supabase refuses to redirect anywhere not on this list after an email link.

### 1.4 Email templates

The frontend confirms email links on the server (section 2.5), so the links must carry a
`token_hash`. In **Authentication → Emails → Templates**, change the link in:

- **Confirm signup**:
  `{{ .SiteURL }}/auth/confirm?token_hash={{ .TokenHash }}&type=email&next=/onboarding`
- **Reset password**:
  `{{ .SiteURL }}/auth/confirm?token_hash={{ .TokenHash }}&type=recovery&next=/reset-password`

For example, the Confirm signup body becomes:

```html
<h2>Confirm your signup</h2>
<p><a href="{{ .SiteURL }}/auth/confirm?token_hash={{ .TokenHash }}&type=email&next=/onboarding">Confirm your email</a></p>
```

### 1.5 Email delivery (before real users)

Supabase's built-in email sender is for testing only. It sends a handful of emails per hour and
only to team members' addresses. Before real users sign up, set up custom SMTP (for example Resend,
Postmark or SendGrid) in **Authentication → Emails → SMTP Settings**.

### 1.6 Database

Tables and Row Level Security are created by the migrations in `supabase/migrations`, which the
Supabase GitHub integration runs when they are merged into `main` (see
[supabase/README.md](../supabase/README.md)). Auth works without
these tables, but profile, friends and calls do not.

### 1.7 Environment files

`backend/.env`:

```dotenv
SUPABASE_URL=https://<project-ref>.supabase.co
SUPABASE_PUBLISHABLE_KEY=sb_publishable_...
```

`frontend/tiwwsah_webapp/.env.local`:

```dotenv
NEXT_PUBLIC_SUPABASE_URL=https://<project-ref>.supabase.co
NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=sb_publishable_...
BACKEND_API_URL=http://localhost:8000
```

---

## 2. Frontend implementation

For the Next.js app in `frontend/tiwwsah_webapp`. This uses `@supabase/ssr`, which keeps the
session in cookies so server components, route handlers and `proxy.ts` can all read it.

> This project uses Next.js 16, where `middleware.ts` is now `proxy.ts` and `cookies()` is async.
> Check `node_modules/next/dist/docs/` (see `AGENTS.md`) if anything below disagrees with the
> installed version.

### 2.1 Install

```bash
cd frontend/tiwwsah_webapp
npm install @supabase/supabase-js @supabase/ssr
```

Add the variables from [1.7](#17-environment-files) to `.env.local`. `BACKEND_API_URL` replaces
the current `BACKEND_API_IP`.

### 2.2 Supabase clients

`lib/supabase/client.ts`, for client components:

```ts
import { createBrowserClient } from "@supabase/ssr";

export function createClient() {
  return createBrowserClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY!,
  );
}
```

`lib/supabase/server.ts`, for server components, server actions and route handlers:

```ts
import { createServerClient } from "@supabase/ssr";
import { cookies } from "next/headers";

export async function createClient() {
  const cookieStore = await cookies();

  return createServerClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY!,
    {
      cookies: {
        getAll() {
          return cookieStore.getAll();
        },
        setAll(cookiesToSet) {
          try {
            cookiesToSet.forEach(({ name, value, options }) =>
              cookieStore.set(name, value, options),
            );
          } catch {
            // Server components cannot set cookies; proxy.ts refreshes the session instead.
          }
        },
      },
    },
  );
}
```

### 2.3 Route protection and session refresh (`proxy.ts`)

Replace the `scamai_session` cookie check. The proxy refreshes the access token when it expires and
redirects signed-out users to `/`.

```ts
import { createServerClient } from "@supabase/ssr";
import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";

const publicRoutes = new Set(["/", "/auth/confirm"]);

export async function proxy(request: NextRequest) {
  let response = NextResponse.next({ request });

  const supabase = createServerClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY!,
    {
      cookies: {
        getAll() {
          return request.cookies.getAll();
        },
        setAll(cookiesToSet) {
          cookiesToSet.forEach(({ name, value }) => request.cookies.set(name, value));
          response = NextResponse.next({ request });
          cookiesToSet.forEach(({ name, value, options }) =>
            response.cookies.set(name, value, options),
          );
        },
      },
    },
  );

  // Validates the session and refreshes it if needed. Keep this directly after
  // createServerClient, and always return `response` so refreshed cookies reach the browser.
  const { data } = await supabase.auth.getClaims();
  const isSignedIn = Boolean(data?.claims);

  if (!isSignedIn && !publicRoutes.has(request.nextUrl.pathname)) {
    return NextResponse.redirect(new URL("/", request.url));
  }

  return response;
}

export const config = {
  matcher: ["/((?!api|_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp)$).*)"],
};
```

Optionally, redirect signed-in users away from `/` to `/dashboard`.

This proxy also needs the MFA redirect from [2.9](#29-multi-factor-authentication-mfa).

Then delete `SESSION_COOKIE_NAME` (`lib/auth.ts`) and the call to `/api/auth/test-session`.

### 2.4 Sign in, sign up and password reset (`components/auth-panel.tsx`)

Use the browser client in the existing form handlers.

```ts
import { createClient } from "@/lib/supabase/client";

const supabase = createClient();
```

**Log in**

```ts
const { error } = await supabase.auth.signInWithPassword({ email, password });

if (error) {
  setError(
    error.code === "email_not_confirmed"
      ? "Please confirm your email first. Check your inbox."
      : "Incorrect email or password.",
  );
  return;
}

router.replace("/dashboard"); // the app layout sends users without a profile to /onboarding
router.refresh();
```

**Register**

```ts
const { data, error } = await supabase.auth.signUp({
  email,
  password,
  options: {
    data: { full_name: name }, // only used to pre-fill the onboarding form
  },
});

if (error) {
  setError(error.code === "weak_password" ? error.message : "Unable to create your account.");
  return;
}

if (!data.session) {
  // Email confirmation is on: the user must click the link in their email.
  setNotice("Check your email to confirm your account.");
  return;
}

router.replace("/onboarding");
router.refresh();
```

With email confirmation on, Supabase does **not** return an error for an email that is already
registered, so other people cannot find out which emails have accounts. Always show the
"check your email" message.

Check that `password` and `confirmPassword` match before calling `signUp`.

**Terms checkbox**: move it to the onboarding form. The backend records privacy policy acceptance
when it creates the user's profile row during onboarding (section 2.7).

**Forgot password**

```ts
await supabase.auth.resetPasswordForEmail(email);
// Always show: "If an account exists, we've sent a reset link."
```

The email link goes to `/auth/confirm?…&next=/reset-password` (see [1.4](#14-email-templates)), which
signs the user in. Add a `/reset-password` page (it does not need to be public in `proxy.ts`) that
calls:

```ts
await supabase.auth.updateUser({ password: newPassword });
```

### 2.5 Email link confirmation (`app/auth/confirm/route.ts`)

Handles the confirm-signup and reset-password emails from [1.4](#14-email-templates).

```ts
import type { EmailOtpType } from "@supabase/supabase-js";
import { NextResponse, type NextRequest } from "next/server";
import { createClient } from "@/lib/supabase/server";

export async function GET(request: NextRequest) {
  const { searchParams, origin } = request.nextUrl;
  const tokenHash = searchParams.get("token_hash");
  const type = searchParams.get("type") as EmailOtpType | null;
  const next = safeNext(searchParams.get("next"));

  if (tokenHash && type) {
    const supabase = await createClient();
    const { error } = await supabase.auth.verifyOtp({ type, token_hash: tokenHash });
    if (!error) {
      return NextResponse.redirect(`${origin}${next}`);
    }
  }

  return NextResponse.redirect(`${origin}/?error=link_expired`);
}

function safeNext(next: string | null) {
  return next?.startsWith("/") && !next.startsWith("//") ? next : "/dashboard";
}
```

Show a message on `/` when `?error=auth` or `?error=link_expired` is present.

### 2.6 Calling the backend (`lib/api.ts`)

Call the backend **from the server** (server components, server actions or route handlers). The
backend has no CORS configuration yet, so browser requests to it would be blocked. If a client
component needs backend data, go through a server action or route handler.

```ts
import "server-only";
import { createClient } from "@/lib/supabase/server";

export async function apiFetch(path: string, init: RequestInit = {}) {
  const supabase = await createClient();
  const {
    data: { session },
  } = await supabase.auth.getSession();

  if (!session) {
    return new Response(null, { status: 401 });
  }

  const headers = new Headers(init.headers);
  headers.set("Authorization", `Bearer ${session.access_token}`);
  if (init.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  return fetch(`${process.env.BACKEND_API_URL}${path}`, {
    ...init,
    headers,
    cache: "no-store",
  });
}
```

`getSession()` is fine here because it only fetches the token to forward. The backend verifies it.
Do not use `getSession()` to decide whether a user is signed in. Use `getClaims()` or `getUser()`.

If a request returns `401`, the session has ended. Send the user to `/`.

### 2.7 Current user and onboarding

Rewrite `lib/get-current-user.ts`:

```ts
import { redirect } from "next/navigation";
import { apiFetch } from "@/lib/api";

export async function getCurrentUser() {
  const response = await apiFetch("/api/v1/users/me");

  if (response.status === 401) return null;     // signed out
  if (response.status === 404) redirect("/onboarding"); // signed in, no profile yet
  if (!response.ok) throw new Error("Unable to retrieve the current user.");

  return response.json();
}
```

The `(app)` layout already redirects to `/` when this returns `null`. The response shape follows
the backend's `UserProfileResponse` (see `/docs` on the backend), not `first_name`/`last_name`.
Show `display_name` in the sidebar.

`GET /users/me` still returns `501` until the backend implements it. Until then, the layout can read
the signed-in user straight from Supabase:

```ts
const supabase = await createClient(); // lib/supabase/server
const { data } = await supabase.auth.getUser();
// data.user?.email, data.user?.user_metadata.full_name (set at sign-up)
```

**Onboarding form**: submit to `POST /api/v1/users/me/onboarding` through a server action using
`apiFetch`, then redirect to `/dashboard` (or `/verify-identity`). The form needs to collect what the
`users` row requires:

- `username` (how friends find each other)
- `display_name` (pre-fill from `user_metadata.full_name`)
- privacy policy acceptance (checkbox)
- the onboarding answers (age range, interests, …)

The exact request body is **TBD** with the backend team; check `/docs` once the endpoint is built.

### 2.8 Sign out (`components/sidebar.tsx`)

```ts
const supabase = createClient(); // lib/supabase/client
await supabase.auth.signOut();
router.replace("/");
router.refresh();
```

### 2.9 Multi-factor authentication (MFA)

Supabase MFA uses authenticator apps (TOTP: Google Authenticator, 1Password, Authy, …). A session
has an **assurance level**:

- `aal1`: signed in with email and password only.
- `aal2`: signed in with email and password **and** a code from the authenticator app.

After `signInWithPassword`, a user who has set up MFA is only at `aal1` until they enter a code.
The app must ask for it before letting them in.

> **TBD: optional or required.** If MFA is optional, users turn it on in Settings. If it is
> required, every user must set it up (for example right after onboarding) before using the app.
> The steps below cover both; the difference is only in the proxy redirect.

**Set up MFA (e.g. `app/(app)/settings/security`, or `/mfa/setup` if required)**

```ts
const supabase = createClient(); // lib/supabase/client

// 1. Create the factor and show its QR code.
const { data, error } = await supabase.auth.mfa.enroll({
  factorType: "totp",
  friendlyName: "Authenticator app",
});
// <img src={data.totp.qr_code} alt="Scan with your authenticator app" />
// Also show data.totp.secret for users who cannot scan the code.

// 2. The user enters the 6-digit code from their app to confirm it works.
const { error: verifyError } = await supabase.auth.mfa.challengeAndVerify({
  factorId: data.id,
  code,
});
// Success: MFA is on, and the session is now aal2.
```

If the user cancels before entering a code, remove the unfinished factor:
`await supabase.auth.mfa.unenroll({ factorId: data.id })`.

**Ask for the code at login (`app/mfa/page.tsx`)**

After a successful `signInWithPassword`, check the assurance level (section 2.4's login handler
should redirect here instead of `/dashboard` when this applies):

```ts
const { data: aal } = await supabase.auth.mfa.getAuthenticatorAssuranceLevel();
const needsCode = aal?.currentLevel === "aal1" && aal?.nextLevel === "aal2";
```

On `/mfa`, verify the code against the user's authenticator factor:

```ts
const { data: factors } = await supabase.auth.mfa.listFactors();
const factor = factors?.totp[0]; // only verified factors are listed here

const { error } = await supabase.auth.mfa.challengeAndVerify({
  factorId: factor!.id,
  code,
});

if (error) {
  setError("That code didn't work. Check your authenticator app and try again.");
  return;
}

router.replace("/dashboard");
router.refresh();
```

**Enforce it in `proxy.ts`**

Without this, a user at `aal1` could skip `/mfa` by typing another URL. After the `getClaims()`
check in section 2.3, add:

```ts
if (isSignedIn) {
  const { data: aal } = await supabase.auth.mfa.getAuthenticatorAssuranceLevel();
  const path = request.nextUrl.pathname;

  // Has MFA set up but has not entered a code this session.
  if (aal?.currentLevel === "aal1" && aal?.nextLevel === "aal2" && path !== "/mfa") {
    return NextResponse.redirect(new URL("/mfa", request.url));
  }

  // Only if MFA is required: has not set it up yet.
  // if (aal?.nextLevel === "aal1" && path !== "/mfa/setup") {
  //   return NextResponse.redirect(new URL("/mfa/setup", request.url));
  // }
}
```

**Turn MFA off (Settings, optional MFA only)**

```ts
await supabase.auth.mfa.unenroll({ factorId });
```

Supabase only allows this from an `aal2` session, so the user must have entered a code first.

**Lost authenticator.** Supabase has no backup codes. Encourage users to add a second authenticator
(the project allows several per user). Otherwise, a team member can remove the factor in the
Supabase dashboard under **Authentication → Users → (user)**, after confirming who they are.

### 2.10 Checklist

- [ ] `@supabase/supabase-js` and `@supabase/ssr` installed, `.env.local` filled in
- [ ] `lib/supabase/client.ts` and `lib/supabase/server.ts`
- [ ] `proxy.ts` uses Supabase; `SESSION_COOKIE_NAME` and `test-session` removed
- [ ] Log in, register and forgot password in `auth-panel.tsx`
- [ ] `app/auth/confirm/route.ts`
- [ ] `/reset-password` page
- [ ] `lib/api.ts`; `getCurrentUser` calls the backend and handles `401` and `404`
- [ ] Onboarding collects username, display name and privacy policy acceptance (terms checkbox moved there)
- [ ] Sign out in the sidebar
- [ ] MFA setup screen (QR code, confirm code, cancel removes the factor)
- [ ] `/mfa` code prompt after login, and the MFA redirect in `proxy.ts`
- [ ] Turn MFA off in Settings (if MFA is optional)

---

## 3. Backend contract

- Every `/api/v1` route requires `Authorization: Bearer <access_token>`. `/health` does not.
- `401`: the token is missing, invalid or expired. The frontend should sign the user out or send
  them to `/`.
- `503`: the backend cannot reach Supabase or is missing its Supabase settings.
- The user is always taken from the token. Never send a user ID to act as.
- `GET /api/v1/users/me` returns `404` for a signed-in user without a profile *(planned)*.
- `POST /api/v1/users/me/onboarding` creates the profile *(planned; body TBD)*.
- **MFA** *(planned)*: the backend will reject `aal1` tokens with `401` for users who have set up
  MFA, so a password alone is never enough to call the API. If MFA becomes required, it will reject
  every `aal1` token. Until this is built, the frontend checks in section 2.9 are the only
  enforcement.

Full API reference: `http://localhost:8000/docs` while the backend is running.

---

## 4. Testing the flow locally

1. Fill in both env files ([1.7](#17-environment-files)).
2. Start the backend: `cd backend && uv run fastapi dev`.
3. Start the frontend: `cd frontend/tiwwsah_webapp && npm run dev`.
4. Register at `http://localhost:3000`. With email confirmation on, use a team member's email (the
   built-in sender only delivers to them) and click the link.
5. Check **Authentication → Users** in the Supabase dashboard: the user is there.
6. To call the backend by hand, get an access token. Either temporarily log
   `session.access_token` in `apiFetch`, or sign in with curl:

   ```bash
   curl -s "https://<project-ref>.supabase.co/auth/v1/token?grant_type=password" \
     -H "apikey: <publishable key>" -H "Content-Type: application/json" \
     -d '{"email": "you@example.com", "password": "..."}'
   ```

   Then call the backend with the `access_token` from the response:

   ```bash
   curl -i http://localhost:8000/api/v1/users/me -H "Authorization: Bearer <token>"
   ```

   Anything except `401` means the token was accepted. `501` is expected until the endpoint is built.
