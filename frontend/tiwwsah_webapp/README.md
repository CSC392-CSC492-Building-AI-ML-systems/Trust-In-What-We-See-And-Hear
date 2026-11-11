# ScamAi frontend

Next.js frontend for the browser-based scam-awareness simulator.

## Run locally

```bash
#to get started first install packages and run nextjs server with:
npm install
npm run dev
```

Then open [http://localhost:3000](http://localhost:3000).

## Supabase login

Copy `.env.example` to `.env.local`, then fill in the values from the Supabase project's Connect dialog:

```bash
NEXT_PUBLIC_SUPABASE_URL=https://your-project-ref.supabase.co
NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=sb_publishable_your_key
```

Email/password authentication and **Confirm email** must be enabled in Supabase. Add the verification return URL (for example, `http://localhost:3000/?email_verified=true`) to Authentication > URL Configuration > Redirect URLs. Registration creates only the Supabase Auth user; the application profile is created later by the Python backend after onboarding.
