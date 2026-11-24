# ScamAi frontend

Next.js frontend for the browser-based scam-awareness simulator.

## Run locally

To run the whole stack (frontend, backend, mock voice pipeline and local Supabase), follow
[Local development](../../README.md#local-development) in the root README.

To run only the frontend, from this folder:

```bash
npm ci
npm run dev
```

It needs `.env.local` (copy `.env.example`, or let the root `npm run setup` create it). Then open
[http://localhost:3000](http://localhost:3000).

## Docs

- [Identity verification](docs/identity-verification.md): the verification endpoints, the
  `/verify-identity` page and what the frontend needs to implement.

## Supabase login
