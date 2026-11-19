# Voice pipeline

The service that runs a simulated call: speech in, the caller's speech out.
It runs separately from the backend API because it needs a GPU.

So far this folder has the WebSocket contract and a mock of it. The real
pipeline (speech to text, LLM, cloned voice) is being ported in.

- [PROTOCOL.md](PROTOCOL.md): the call WebSocket, message by message.
- [BACKEND.md](BACKEND.md): what the pipeline needs from the backend to start
  a call and hand back its result (a proposal).
- `mock_server.py`: implements the protocol with no GPU and no models.

## Run the mock

`npm run dev` and `npm run docker` at the repository root start the mock along with everything
else, and point the backend at it (see [Local development](../README.md#local-development)).

To run it on its own: it only needs FastAPI, so the backend's environment works:

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

## Tests

```bash
cd backend
uv run pytest ../voice-pipeline/tests
```

AI-assisted documentation (written with Claude Code), cited per the CSC398 AI
policy.
