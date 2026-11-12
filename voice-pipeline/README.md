# Voice pipeline

The service that runs a simulated call: speech in, the caller's speech out.
It runs separately from the backend API because it needs a GPU.

So far this folder has the WebSocket contract and a mock of it. The real
pipeline (speech to text, LLM, cloned voice) is being ported in.

- [PROTOCOL.md](PROTOCOL.md): the call WebSocket, message by message.
- `mock_server.py`: implements the protocol with no GPU and no models.

## Run the mock

It only needs FastAPI, so the backend's environment works:

```bash
cd backend
uv run uvicorn --app-dir ../voice-pipeline mock_server:app --port 8001
```

Then connect to `ws://localhost:8001/ws/calls/anything?token=dev`.

What the mock does differently from the real pipeline:

- the caller's voice is a tone, and its lines are fixed
- the transcript is a placeholder, not what was said
- speech is detected by loudness, so it needs a quiet room
- any call ID and any non-empty token are accepted

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
