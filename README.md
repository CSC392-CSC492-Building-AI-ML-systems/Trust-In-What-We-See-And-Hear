# Trust-In-What-We-See-And-Hear

| Folder | What it is |
|---|---|
| [`frontend/tiwwsah_webapp`](frontend/tiwwsah_webapp/README.md) | Next.js web app |
| [`backend`](backend/README.md) | FastAPI API |
| [`voice-pipeline`](voice-pipeline/README.md) | Voice pipeline (needs a GPU), its call WebSocket protocol and a mock of it |
| [`supabase`](supabase/README.md) | Database migrations and local Supabase config |
| [`docs`](docs/README.md) | Authentication and other design docs |

## Local development

You can run the app on your machine in two ways. Both run the same four parts:

- the frontend
- the backend
- the mock voice pipeline
- a local Supabase stack (Postgres, Auth and Storage) with every migration in
  `supabase/migrations` applied

The local Supabase stack is separate from the hosted project, so nothing you do locally touches
real data. It runs through the [Supabase CLI](https://supabase.com/docs/guides/local-development),
which needs Docker, so **both options need Docker running** (Docker Desktop on Windows and macOS).

Which option to use:

- **Option 1, native**: servers start and reload faster, and you can attach a debugger. You
  install Node.js, uv and ffmpeg.
- **Option 2, Docker**: you install nothing besides Docker and Node.js.

### Option 1: run natively

You need:

- Docker, running
- [Node.js](https://nodejs.org/) 22.13 or newer
- [uv](https://docs.astral.sh/uv/getting-started/installation/), which installs the right Python
  for you
- [ffmpeg](https://ffmpeg.org/download.html) on `PATH`, for voice uploads (`winget install ffmpeg`,
  `brew install ffmpeg` or `apt install ffmpeg`)

From the repository root:

```bash
npm run setup   # once: installs dependencies, starts Supabase and creates the env files
npm run dev     # each time: starts the frontend, backend and pipeline mock
```

Ctrl+C stops the frontend, backend and mock. Supabase keeps running in Docker; see
[Supabase commands](#supabase-commands) to stop it.

`npm run setup` creates two env files that point at the local stack: `backend/.env` and
`frontend/tiwwsah_webapp/.env.local`. It never overwrites them. To regenerate one, delete it and
run `npm run setup` again. To use the hosted Supabase project instead, put its URL and keys in
those files (see [docs/authentication.md](docs/authentication.md)).

You can also run one part on its own, for example `npm run dev` in `frontend/tiwwsah_webapp` or
`uv run fastapi dev` in `backend`.

### Option 2: run in Docker

You need:

- Docker, running, with Compose 2.25 or newer
- [Node.js](https://nodejs.org/) 22.13 or newer, which runs the start script and the Supabase CLI

You do not need to run `npm install` or `npm run setup` first. From the repository root:

```bash
npm run docker
```

This starts Supabase with the CLI version pinned in `package.json`, then runs
`docker compose up --build --watch --force-recreate`.

While it runs, edits to the source are copied into the containers and the servers reload.
Changing a `package.json`, `package-lock.json`, `pyproject.toml` or `uv.lock` rebuilds that image.
Ctrl+C stops the containers, and `docker compose down` removes them. This option does not use the
env files from option 1.

### Where things are

| What | URL |
|---|---|
| Frontend | <http://localhost:3000> |
| API docs | <http://localhost:8000/docs> |
| Voice pipeline mock | <http://localhost:8001> |
| Supabase Studio (database UI) | <http://127.0.0.1:54323> |
| Emails sent by local Auth | <http://127.0.0.1:54324> |

The backend and mock ports can be changed; see [Ports](#ports).

### Demo accounts

Each time `npm run setup`, `npm run dev` or `npm run docker` starts, it loads demo data from
[`supabase/local-dev-data.sql`](supabase/local-dev-data.sql) into the local stack. If you changed
or deleted that data, it is put back. Sign in with either account:

| Email | Password | Username |
|---|---|---|
| `alice@example.com` | `demo-password` | `alice_demo` |
| `bob@example.com` | `demo-password` | `bob_demo` |

Both have finished onboarding and are friends. Each has an unused consent to the other's voice, and
there is one scenario to run.

Use these accounts rather than signing up. Sign-up works locally without email confirmation, but
the frontend does not create a profile for the new account yet, so pages that need one show errors.

To start without the demo data, add `-- --no-demo-data` to the command, for example
`npm run dev -- --no-demo-data`. This only skips loading it: data from an earlier start stays until
you run `npm run supabase db reset`.

### Calls and the mock pipeline

Starting a call (`POST /api/v1/calls`) works in both options. The backend returns a `ws_url` on the
mock, and the mock plays a whole call over it.

The mock does not report back to the backend, so the backend never learns how the call went. Its
copy of the call stays `queued`. The first time the call's owner reads it, or their call history,
more than two minutes after it started, it changes to `failed` (`timed_out`). The mock's own canned
result is at `GET http://localhost:8001/api/v1/calls/<call id>`.

The real voice pipeline needs a large NVIDIA GPU, so neither option runs it. The team tests it on a
rented GPU with `voice-pipeline/dev/pod.py` (see [voice-pipeline/dev/POD.md](voice-pipeline/dev/POD.md)).
By default that pod uses its own test page and token. To connect it to your local backend instead,
follow "With the backend" in POD.md: it tunnels the backend to the pod, and
`npm run dev -- --pipeline` then starts the stack with no mock, using the pod's settings from
`backend/.env`. `npm run docker` always uses the mock.

### Tests

```bash
npm test
```

runs the backend's tests and the voice pipeline's, in the backend's environment. Neither needs
Docker, Supabase or a GPU. It runs both even if the first fails. To run one, use `uv run pytest` in
`backend`, or `uv run pytest ../voice-pipeline/tests` there.

Some tests are skipped, not failed, when what they need is missing: the voice upload tests without
ffmpeg, and a few pipeline tests that need the real pipeline's model packages
(`voice-pipeline/requirements.txt`). The summary line shows how many were skipped.

### Supabase commands

Run these from the repository root. If you use option 2, or have not run `npm run setup`, replace
`npm run supabase` with `npx --yes supabase@3.1.0`.

| Command | What it does |
|---|---|
| `npm run supabase stop` | Stops the local stack and keeps its data. `npm run dev` or `npm run docker` starts it again. |
| `npm run supabase migration up` | Applies new migrations while everything keeps running. |
| `npm run supabase db reset` | Deletes all local data and reapplies every migration. The next start loads the demo data again, unless you pass `--no-demo-data`. |
| `npm run supabase status` | Shows the local URLs and keys. |

The local stack uses publicly known keys and the demo passwords above, and the Supabase CLI makes
its ports reachable from your network. Stop it when you are on a network you do not trust.

### Ports

The frontend always uses port 3000, because backend CORS and the Auth redirect URLs expect it.

The backend uses 8000 and the mock uses 8001. To change them, copy `.env.example` to `.env` at the
repository root and set `BACKEND_PORT` or `PIPELINE_PORT`. Both options read this file and point
the frontend at the new backend port. A frontend you start on its own reads the backend URL from
`NEXT_PUBLIC_API_URL` in `frontend/tiwwsah_webapp/.env.local` instead.

### Troubleshooting

- **"Port … is in use or reserved"**: another program has that port. `npm run dev` checks before
  starting anything. Stop the other program, or set a different port as described in
  [Ports](#ports). If the program is a previous `npm run docker`, run `docker compose down`.
- **"Could not apply the database migrations"** after switching branches: your local database has
  migrations this branch does not. Starting carries on anyway. Run `npm run supabase db reset` to
  rebuild the database for the current branch.
- **Every API call fails with 401, or the demo accounts do not exist**: `backend/.env` or
  `frontend/tiwwsah_webapp/.env.local` points at another Supabase project, often the hosted one
  from before `npm run setup` existed. `npm run dev` warns about this when it starts. Delete the
  file and run `npm run setup` again.
- **"Docker is not running"**: start Docker Desktop, or the Docker daemon on Linux, and run the
  command again.
