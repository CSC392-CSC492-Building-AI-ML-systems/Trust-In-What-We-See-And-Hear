# Notes for coding agents

A scam-call training app: the user practises spotting a scam call from a
friend's cloned voice, with the friend's consent. Three parts:

- `frontend/tiwwsah_webapp/`: Next.js app.
- `backend/`: FastAPI API, with Supabase for the database, auth and storage.
- `voice-pipeline/`: the call itself (speech in, cloned speech out, then a
  score). It needs a large GPU, so it runs on a rented one.

`frontend/tiwwsah_webapp/AGENTS.md` and `voice-pipeline/AGENTS.md` have
rules for working in those folders. Each folder also has a one-line
`CLAUDE.md` that imports its `AGENTS.md`, so Claude Code loads it there.

## Where the answers are

Read these before asking a teammate. Most questions about how the parts
talk to each other are answered in them.

| Question | Read |
|---|---|
| Which endpoints exist, and their request and response shapes | The backend's generated docs at `/docs` on a running backend (`backend/README.md` says how to run it) |
| What a call context looks like (persona, target, scenario, details) | `backend/app/modules/calls/README.md` and `backend/app/modules/calls/sample_call_context.json` |
| How the browser starts a call and gets its `ws_url` | `voice-pipeline/BACKEND.md`, sections 1 and 4 |
| The call WebSocket: messages, their order, errors, turn-taking | `voice-pipeline/PROTOCOL.md` (a full exchange is under "Example") |
| Audio formats for the microphone and the caller | `voice-pipeline/PROTOCOL.md`, "Audio formats" |
| The score and feedback after a call | `voice-pipeline/PROTOCOL.md`, "Call results" |
| How the backend and the pipeline talk (claim and result) | `voice-pipeline/BACKEND.md`, sections 2 and 3 |
| Voice recording limits (length, size, loudness, formats) | `voice_sample_*` in `backend/app/core/config.py`, `SUPPORTED_FORMATS` in `backend/app/modules/voices/audio.py`, and the voice upload route in `/docs` |
| Tables, row level security, retention, which endpoint uses which table | `supabase/database-schema.md` |
| Sign-in on the frontend and token checks on the backend | `docs/authentication.md` |
| Migrations and changing the schema | `supabase/README.md` |

## Building without a GPU

The mock pipeline speaks the same WebSocket protocol with no models (a tone
for a voice, fixed lines, canned results). `npm run setup`, then `npm run dev`
at the root runs the frontend, the backend and the mock together (see
"Local development" in `README.md`). To run the mock on its own, see "Run
the mock" in `voice-pipeline/README.md`. To try the real pipeline, rent a GPU
with `voice-pipeline/dev/pod.py` (guide: `voice-pipeline/dev/POD.md`; it
costs money by the hour).

## Ground rules

- The app keeps no call transcripts: the text of a call lives only in the
  pipeline's memory during the call.
- A call needs an accepted consent from the friend whose voice is used, and
  each consent covers one call.
- Never commit `.env` files or keys.
- AI-assisted code and docs carry a line saying so, per the course's AI
  policy (see existing files for the wording).

AI-assisted documentation (written with Claude Code), cited per the CSC398
AI policy.
