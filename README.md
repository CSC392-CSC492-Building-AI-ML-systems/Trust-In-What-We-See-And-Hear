# Trust-In-What-We-See-And-Hear

| Folder | What it is |
|---|---|
| [`frontend/tiwwsah_webapp`](frontend/tiwwsah_webapp/README.md) | Next.js web app |
| [`backend`](backend/README.md) | FastAPI API |
| [`voice-pipeline`](voice-pipeline/README.md) | Call WebSocket protocol and a mock of the voice pipeline |
| [`supabase`](supabase/README.md) | Database migrations and local Supabase config |
| [`docs`](docs/README.md) | Authentication and other design docs |

## Local development

Both ways below run the same things: the frontend, the backend, the mock voice pipeline and a local
Supabase stack (Postgres, Auth, Storage) with every migration in `supabase/migrations` applied. The
local stack is separate from the hosted project, so nothing you do touches real data.

Supabase always runs from the [Supabase CLI](https://supabase.com/docs/guides/local-development),
which needs **Docker** in both cases (Docker Desktop on Windows and macOS).

### Option 1: run natively

Needs Docker (running), [Node.js](https://nodejs.org/) 22.13 or newer,
[uv](https://docs.astral.sh/uv/getting-started/installation/) and
[ffmpeg](https://ffmpeg.org/download.html) on `PATH` for voice uploads (`winget install ffmpeg`,
`brew install ffmpeg` or `apt install ffmpeg`).

```bash
npm run setup   # once: installs dependencies, starts Supabase, creates the env files
npm run dev     # every time: frontend, backend and pipeline mock together; Ctrl+C stops them
```

`npm run setup` creates `backend/.env` and `frontend/tiwwsah_webapp/.env.local` pointing at the
local stack. It never overwrites them; delete one and rerun to regenerate it. To use the hosted
Supabase project instead, put its URL and keys in those files (see
[docs/authentication.md](docs/authentication.md)).

You can still run one part on its own, e.g. `npm run dev` inside `frontend/tiwwsah_webapp`, or
`uv run fastapi dev` inside `backend`.

### Option 2: run in Docker

Needs Docker (running) with Compose 2.25 or newer, and Node.js 22.13 or newer (only to run the
Supabase CLI). Nothing else, and no `npm install` first.

```bash
npm run docker
```

This starts Supabase with the CLI version pinned in `package.json`, and then runs
`docker compose up --build --watch --force-recreate`, which you can also run yourself once
Supabase is up.

Edits to the source are synced into the containers and reload the servers. Changing a
`package.json`, `package-lock.json`, `pyproject.toml` or `uv.lock` rebuilds the image. Ctrl+C
stops the containers; `docker compose down` removes them. The env files from option 1 are not
used.

### Where things are

| | URL |
|---|---|
| Frontend | <http://localhost:3000> |
| API docs | <http://localhost:8000/docs> |
| Voice pipeline mock | <http://localhost:8001> |
| Supabase Studio (database UI) | <http://127.0.0.1:54323> |
| Emails sent by local Auth | <http://127.0.0.1:54324> |

### Demo accounts

Every start applies new migrations and restores this data from
[`supabase/local-dev-data.sql`](supabase/local-dev-data.sql), in the local stack only:

| Email | Password | Username |
|---|---|---|
| `alice@example.com` | `demo-password` | `alice_demo` |
| `bob@example.com` | `demo-password` | `bob_demo` |

They have finished onboarding and are friends, each has an unused consent to the other's voice, and
there is one scenario. Use them to try the app: the sign-up page works locally without email
confirmation, but it does not create a profile yet, so a new account gets errors from pages that
need one.

To start without loading it, add `-- --no-demo-data`, e.g. `npm run dev -- --no-demo-data` or
`npm run docker -- --no-demo-data` (also works with `npm run setup`). Data loaded by an earlier
start stays; `npm run supabase db reset` removes it.

### Calls and the mock pipeline

`POST /api/v1/calls` works in both options and returns a `ws_url` on the mock, which then plays a
whole call. The mock does not report back to the backend (no claim or result), so the backend's
call stays `queued`. The first time its owner reads the call or their history more than two minutes
after it started, it becomes `failed` (`timed_out`). The canned result is at the mock's own
`GET /api/v1/calls/<call id>`.

### Supabase commands

Run these from the repository root. With option 2, or before `npm run setup` has run, replace
`npm run supabase` with `npx --yes supabase@3.1.0`.

| Command | What it does |
|---|---|
| `npm run supabase stop` | Stops the local stack. Data is kept. `npm run dev` or `npm run docker` starts it again. |
| `npm run supabase migration up` | Applies new migrations without restarting anything. |
| `npm run supabase db reset` | Deletes all local data and reapplies every migration. The next start restores the demo data. |
| `npm run supabase status` | Shows the local URLs and keys. |

If you switch to a branch with fewer migrations than your local database, starting prints a warning
and carries on. `npm run supabase db reset` rebuilds the database for the current branch.

The local stack uses well-known keys and the demo passwords above, and the Supabase CLI makes its
ports reachable from your network, so stop it (`npm run supabase stop`) on a network you do not
trust.

### Ports

The frontend always uses port 3000; backend CORS and the Auth redirect URLs expect it. If 8000 or
8001 is taken on your machine, copy `.env.example` to `.env` at the repository root and set
`BACKEND_PORT` or `PIPELINE_PORT`. Both options read it and point the frontend at the new backend
port. (Only a frontend started on its own reads the port from `NEXT_PUBLIC_API_URL` in
`frontend/tiwwsah_webapp/.env.local`.)
