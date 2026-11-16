# Voice pipeline

The service that runs a simulated call: speech in, the caller's speech out.
It runs separately from the backend API because it needs a GPU.

- [PROTOCOL.md](PROTOCOL.md): the call WebSocket, message by message.
- [BACKEND.md](BACKEND.md): what the pipeline needs from the backend to start
  a call and hand back its result (a proposal).
- `mock_server.py`: implements the protocol with no GPU and no models.
- `pipeline/`: the real server. Same WebSocket, real models.

## Run the mock

It only needs FastAPI, so the backend's environment works:

```bash
cd backend
uv run uvicorn --app-dir ../voice-pipeline mock_server:app --port 8001
```

Then `POST http://localhost:8001/api/v1/calls` gives a call `id` and a
`ws_url` to connect to (or connect straight to
`ws://localhost:8001/ws/calls/anything?token=dev`). After the call ends,
`GET http://localhost:8001/api/v1/calls/<id>` returns a canned call result
(see "Call results" in PROTOCOL.md).

What the mock does differently from the real pipeline:

- the caller's voice is a tone, and its lines are fixed
- the transcript is a placeholder, not what was said
- speech is detected by loudness, so it needs a quiet room
- any call ID and any non-empty token are accepted
- `completed` is sent without waiting for the last turn to be played
- call results are canned, not scored from the call

Settings are environment variables, listed at the top of `mock_server.py`.
For example, `MOCK_MAX_SECONDS=20` to see the `time_limit` ending, or
`MOCK_END_AFTER_TURNS=3` to see `completed`.

## The real pipeline

Run once on a GPU (2026-11-12, an L40): scripted four-turn calls worked end
to end. Call scoring was added after that run and has only been tested with
a fake LLM. The tests use fake models.

```
microphone -> segmenter -> speech to text -> LLM -> chunker -> voice
```

| File | Does |
|---|---|
| `session.py` | One call: the protocol, turn-taking, endings |
| `models.py` | Runs the models in threads, one GPU job at a time |
| `segmenter.py` | Finds where the user stops talking (Silero VAD) |
| `stt.py` | Speech to text (faster-whisper), rejects noise |
| `llm.py` | Streams the reply from vLLM |
| `prompt.py` | The caller's instructions, built from the call context |
| `chunker.py`, `reply_filter.py` | Cut the reply into speakable chunks and clean it |
| `tts/chatterbox.py` | The voice (Chatterbox). Other engines plug in here, see `parts.py` |
| `calls.py` | Where a call's context and voice recording come from, and where its result goes |
| `scoring.py` | Scores a finished call with the LLM and checks the answer |
| `server.py` | Loads the models and starts the server |

Things it guarantees:

- The conversation is held in memory for the call only. It is never written
  to disk or to logs. When the call ends it is scored, and then cleared.
- The score and summary are checked before they leave: they cannot carry an
  email, website or phone number that was said on the call.
- The persona's voice is loaded from their recording for each call and
  dropped when the call ends.
- Any email, website or phone number the caller says is rewritten to a fake
  one (`@example.com`, `555-01xx`).

To run it on a fresh GPU pod (the scripts say which image and ports):

```bash
bash voice-pipeline/pod/setup.sh   # once: installs everything, starts the LLM
PIPELINE_DEV_TOKEN=... PIPELINE_DEV_VOICE=/path/to/voice.wav \
  bash voice-pipeline/pod/run.sh   # the call server, on port 8000
```

Run like that, every call uses that one token, that one voice recording and
the backend's sample call context, and its result can be read from
`GET /api/v1/calls/<id>?token=...` on the same server. With `PIPELINE_BACKEND_URL` and
`PIPELINE_BACKEND_SECRET` set instead, each call is checked with the backend
(BACKEND.md), which does not have those endpoints yet. Other settings are
environment variables, listed at the top of each module.

## Tests

No GPU needed:

```bash
cd backend
uv run pytest ../voice-pipeline/tests
```

AI-assisted documentation (written with Claude Code), cited per the CSC398 AI
policy.
