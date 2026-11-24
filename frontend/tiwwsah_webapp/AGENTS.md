<!-- BEGIN:nextjs-agent-rules -->

## This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` (resolved from this file's directory; in monorepos the `next` package may not be visible from the repo root) before writing any code. Heed deprecation notices.

This block is written and re-added by `next dev` — verify at `node_modules/next/dist/server/lib/generate-agent-files.js`. Removing it from a diff only re-creates the uncommitted change; committing it with your work keeps the tree clean.

<!-- END:nextjs-agent-rules -->

## Where the contracts are

Paths are from the repository root. `AGENTS.md` there has the full list.

- Endpoints and their shapes: the backend's generated docs at `/docs` on a running backend.
- Sign-in and sending the token to the backend: `docs/authentication.md` (section 2.6 for calling the backend).
- Starting a call: `POST /api/v1/calls` returns a `ws_url` to connect to (`voice-pipeline/BACKEND.md`, section 1).
- The call WebSocket (messages, audio formats, turn-taking, errors, results): `voice-pipeline/PROTOCOL.md`.
- The result after a call: poll `GET /api/v1/calls/<id>` until `status` is `completed` or `failed` (`voice-pipeline/PROTOCOL.md`, "Call results"). The full response, including `failure_reason` for a failed call, is in `/docs`; the examples in PROTOCOL.md and the mock leave fields out.
- What a call context contains: `backend/app/modules/calls/sample_call_context.json`.
- Voice recording limits for uploads: `voice_sample_*` in `backend/app/core/config.py`, and the accepted formats in `SUPPORTED_FORMATS` in `backend/app/modules/voices/audio.py`.
- Building call screens without a GPU: the mock pipeline, under "Run the mock" in `voice-pipeline/README.md`.
