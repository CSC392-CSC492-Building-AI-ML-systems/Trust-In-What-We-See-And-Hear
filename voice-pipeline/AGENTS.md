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
- `pod/`: scripts that set up and run the server on a GPU machine
  (`setup.sh`, `run.sh`, `voice_engine.sh`, `ming_server.py`).
- `tests/`: run without a GPU, using fake models behind the same boundaries.

## Run the tests

From the repo root, with the backend's environment:

```bash
cd backend
uv run pytest ../voice-pipeline/tests
```

Run them after every change. The backend's own tests are `uv run pytest`
in `backend/`. `tests/test_pipeline_segmenter.py` needs torch and silero-vad,
so it is skipped in the backend's environment. Run it where
`requirements.txt` is installed when you change `segmenter.py`.

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

- Course policy: AI-written code must be cited. Each file says so in its
  header ("AI-assisted code (written with ..., cited per the CSC398 AI
  policy."). Add that line, naming the tool used, to new files and keep it
  on existing ones.
- Never start or leave a GPU pod running unless the person you are working
  with asks for it. It bills the team by the hour.

## Current state (2026-11-17)

- The mock (PR #13) is merged into `main`, with its `POST /api/v1/calls`,
  call results and `BACKEND.md`. `main`'s `BACKEND.md`, `PROTOCOL.md` and
  `README.md` differ from this branch's only where they mention `pipeline/`.
  The real pipeline is draft PR #14 (branch `voice-pipeline-port`).
- Two GPU runs so far. 2026-11-16 (A40): a person called it with their own
  recorded voice; first audio about 1.2 s, scoring about 4 s, the
  `completed` ending worked. Found there: the cloned voice sounded little
  like the speaker, replies ran 10 to 12 s, the caller changed its story
  and kept pushing after a yes, and every call was graded
  `recognized_scam` 90. Not yet tried: Chatterbox Turbo.
- 2026-11-17 (L40, then L40S): the caller prompt, reply limits and grading
  `score-v2` worked in calls with a person, on Qwen3-8B and then
  Qwen3-14B-FP8. Qwen3-32B does not fit beside the speech models on 48 GB;
  `pod/setup.sh` now refuses a model that cannot fit.
- Voice: on an H100, Ming-3.0-tts-0.5B and Fish Audio Drama 3 both sounded
  much closer to the speaker than Chatterbox (one listener). Ming is
  preferred so far, for its size. Both are wired in as `TTS_ENGINE=ming` and `drama`
  (`tts/served.py`, `pod/voice_engine.sh`, `pod/ming_server.py`) but have
  not run inside a call yet. Drama 3 needs an 80 GB card.
- Before a GPU session, check a model's size against the GPU and read its
  install notes before renting: most failed GPU sessions so far skipped one
  of those.
- `BACKEND.md` is this side's proposal for how the backend starts a call and
  receives its result. The backend side has not agreed to it or built it yet.
  Change it together with `calls.BackendCalls`, the mock and `PROTOCOL.md`.
- Call setup: `calls.BackendCalls` (claims the call from the backend) when
  `PIPELINE_BACKEND_URL` is set, otherwise `calls.DevCalls` (one token, one
  voice file, the sample context). Only `DevCalls` has run for real.
- Call scoring (`scoring.py`): when a call ends, `session.wrap_up` scores it
  in a background task, reports it through `calls.report`, and clears the
  conversation. The LLM answers plain questions (did the trainee agree,
  refuse or stay undecided; whether payment was completed; which details
  they gave; which tactics were used) and `parse_result` works out the outcome and keeps the score in
  that outcome's range. Asking the LLM for the outcome directly was tried
  first and it could be talked into full marks by the trainee.
- Completed payment or credential disclosure takes precedence over a later
  refusal. Required grading evidence is validated before deriving outcomes.
  `eval_grading.py` checks ten synthetic cases against a running LLM.
- Prompts can be tried without a GPU against Qwen3 8B on Ollama. Ollama
  ignores `repetition_penalty`, so it repeats itself more than vLLM does.

AI-assisted documentation (written with Claude Code and Codex), cited per the CSC398 AI
policy.
