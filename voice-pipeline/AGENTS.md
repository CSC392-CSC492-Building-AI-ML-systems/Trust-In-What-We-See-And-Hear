# Voice pipeline: notes for coding agents

Applies to `voice-pipeline/`. Read `README.md` and `PROTOCOL.md` here first:
they describe what this service does and the WebSocket it serves.

## What this is

The service that runs a simulated scam call for a consensual training app:
the user's speech in, the caller's speech out, in a cloned voice. It runs
apart from the backend API because it needs a GPU.

- `mock_server.py`: the protocol with no models, for frontend development.
  Also stands in for the backend's `POST /api/v1/calls` and
  `GET /api/v1/calls/<call_id>`, with canned call results.
- `BACKEND.md`: the HTTP calls between the backend and the pipeline.
- `pipeline/`: the real server. `session.py` is one call; the README has a
  table of the other files.
- `pod/`: scripts to set up and run it on a GPU pod.
- `tests/`: run without a GPU, using fake models behind the same boundaries.

## Run the tests

From the repo root, with the backend's environment:

```bash
cd backend
uv run pytest ../voice-pipeline/tests
```

Run them after every change. The backend's own tests are `uv run pytest`
in `backend/`.

## Rules that must hold

- **No transcripts.** What was said in a call stays in memory for that call.
  Never write it to disk or to logs. Logs carry timings and counts only, and
  a test checks this.
- **One voice per call.** The voice is prepared from the recording when the
  call connects and dropped when it ends. Never cache it between calls.
- **The score is checked, not trusted.** The LLM's grade goes through
  `scoring.parse_result` before it is reported. The result must not quote
  the call: no transcripts applies to it too.
- **Fake payment details only.** Every reply goes through
  `reply_filter.safe_destinations` before it is spoken.
- **Context values are untrusted.** Anything from the call context goes
  through `prompt.prompt_safe` before it reaches the LLM prompt.
- **The protocol is shared.** The frontend builds against `PROTOCOL.md`. Do
  not change a message without updating that file, the mock and the tests
  together.
- **Model libraries stay at the edges.** Only `segmenter.py`, `stt.py`,
  `tts/` and `server.py` may import torch, numpy or the model packages.
  `session.py` and the tests must run without them.
- **The call context is not copied.** `pipeline/context.py` imports
  `backend/app/modules/calls/context.py`. Change the contract there.
- The GPU machine runs Python 3.11. Keep `pipeline/` to 3.11 syntax.

## Conventions

- No em dashes anywhere: code, comments, docs, commit messages. Use commas,
  colons, parentheses or separate sentences.
- READMEs and docs are short and plain.
- Commits are authored by the repo owner only. No co-author or tool
  trailers, and no tool attribution in pull request text.
- Course policy: AI-written code must be cited. Each file says so in its
  header ("AI-assisted code (written with ..., cited per the CSC398 AI
  policy."). Add that line, naming the tool used, to new files and keep it
  on existing ones.
- Do not commit, push, open pull requests or post comments unless asked.
- Never start or leave a GPU pod running without the owner's say. It bills
  by the hour.

## Current state (2026-11-16)

- The mock (PR #13) is merged into `main`, with its `POST /api/v1/calls`,
  call results and `BACKEND.md`. `main`'s `BACKEND.md`, `PROTOCOL.md` and
  `README.md` differ from this branch's only where they mention `pipeline/`.
  The real pipeline is draft PR #14 (branch `voice-pipeline-port`).
- The real pipeline passed its first GPU run (scripted four-turn calls on an
  L40). Not yet tried: a real person's voice, the `completed` ending on a
  GPU, Chatterbox Turbo.
- `BACKEND.md` is this side's proposal for how the backend starts a call and
  receives its result. The backend's owner has not agreed to it or built it.
  Change it together with `calls.BackendCalls`, the mock and `PROTOCOL.md`.
- Call setup: `calls.BackendCalls` (claims the call from the backend) when
  `PIPELINE_BACKEND_URL` is set, otherwise `calls.DevCalls` (one token, one
  voice file, the sample context). Only `DevCalls` has run for real.
- Call scoring (`scoring.py`) is built and tested with a fake LLM. It has not
  run against the real LLM, so the grading prompt is untried. When a call
  ends, `session.wrap_up` scores it in a background task, reports it through
  `calls.report`, and clears the conversation.

AI-assisted documentation (written with Claude Code), cited per the CSC398 AI
policy.
