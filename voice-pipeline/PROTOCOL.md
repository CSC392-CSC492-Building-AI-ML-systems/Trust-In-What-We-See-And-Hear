# Call audio WebSocket (draft v0)

One WebSocket is one call. The browser streams the microphone continuously
(hands-free) and receives the caller's speech as audio chunks with their text.

Status: draft. `mock_server.py` implements exactly this, so the frontend can
build against it without a GPU. Items marked **TBD** are not settled.

## Connecting

```
ws://<host>/ws/calls/<call_id>?token=<token>
```

- `call_id` and `token` will come from `POST /api/v1/calls` (**TBD** with the
  backend; that endpoint is still a stub). The token is short-lived and goes in
  the query string because browsers cannot set headers on a WebSocket.
- The mock accepts any `call_id` and any non-empty `token`.
- A missing or invalid token closes the socket with code `4401`.

## Messages

Text frames are JSON objects with a `type`. Binary frames are microphone audio.

### Client to server

| Message | Fields | Meaning |
|---|---|---|
| `answered` | | The user picked up. The caller speaks first, after this. The call timer starts here. |
| binary frame | | Microphone audio (format below). Send continuously after `answered`. |
| `played` | `turn_id` | The browser finished playing all audio of that turn. The server listens again after this. |
| `hangup` | | The user ended the call. |
| `ping` | | Optional keep-alive. Answered with `pong`. |

### Server to client

| Message | Fields | Meaning |
|---|---|---|
| `ready` | `call_id`, `input_sample_rate`, `output_sample_rate`, `max_duration_seconds` | Sent once after connecting. Wait for it before sending `answered`. |
| `speech_end` | `seconds` | The server decided the user stopped talking. A reply is coming. |
| `transcript` | `turn_id`, `text` | What the server heard. Showing it is optional. |
| `audio` | `turn_id`, `seq`, `text`, `audio` | One piece of the caller's reply. Play pieces in `seq` order, back to back. |
| `turn_done` | `turn_id` | No more audio for this turn. |
| `call_ended` | `reason` | The call is over. The server closes the socket (code `1000`) right after. |
| `error` | `code`, `message` | Something went wrong. The call continues unless `call_ended` follows. |
| `pong` | | Reply to `ping`. |

`turn_id` is an integer that increases by one per caller reply. The opening
line is turn 1.

`call_ended.reason` is one of:

- `hangup`: the client sent `hangup`
- `time_limit`: the scenario's `max_duration_seconds` ran out
- `completed`: the simulated scam reached its end
- `error`: the pipeline failed and cannot continue

Show the "this was a simulation" reveal on any `call_ended`.

## Audio formats

**Microphone (client to server):** raw PCM, 16 kHz, mono, signed 16-bit
little-endian, no header. Any frame size works; about 40 ms (640 samples,
1280 bytes) per frame is a good default. Browsers capture at 44.1 or 48 kHz
as float samples, so the client has to resample and convert. Turn on the
browser's `echoCancellation` when requesting the microphone.

**Caller (server to client):** each `audio` message carries one complete WAV
file (24 kHz, mono, 16-bit) as base64 in the `audio` field. Decode each with
`AudioContext.decodeAudioData` and queue them. `text` is the words in that
piece; it can be an empty string when one sentence is split over several
pieces.

## Turn-taking

1. After `answered`, the server sends turn 1 (the caller's opening).
2. While a turn is being sent, and until the client sends `played` for it,
   microphone audio is ignored. This stops the caller from hearing itself
   through the speakers. Keep sending audio anyway; the server drops it.
3. After `played`, the server listens. When it hears speech followed by about
   600 ms of silence, it sends `speech_end` and then the next turn.
4. If `played` never arrives, the server starts listening again on its own
   once the turn's audio would have finished playing, plus one second.

Talking over the caller (interruption) is not supported yet.

## Example

```
server  {"type": "ready", "call_id": "abc", "input_sample_rate": 16000,
         "output_sample_rate": 24000, "max_duration_seconds": 300}
client  {"type": "answered"}
server  {"type": "audio", "turn_id": 1, "seq": 0, "text": "Hey, it's me.", "audio": "UklGR..."}
server  {"type": "audio", "turn_id": 1, "seq": 1, "text": "Can you hear me okay?", "audio": "UklGR..."}
server  {"type": "turn_done", "turn_id": 1}
client  {"type": "played", "turn_id": 1}
client  <binary frames, the user talks, then stops>
server  {"type": "speech_end", "seconds": 2.1}
server  {"type": "transcript", "turn_id": 2, "text": "Who is this?"}
server  {"type": "audio", "turn_id": 2, "seq": 0, "text": "...", "audio": "UklGR..."}
server  {"type": "turn_done", "turn_id": 2}
client  {"type": "played", "turn_id": 2}
client  {"type": "hangup"}
server  {"type": "call_ended", "reason": "hangup"}
```

## Call results

Not built yet. The plan: when a call ends, the pipeline scores it and writes
the summary from the conversation it still holds in memory, then hands the
result to the backend to store (the app does not keep transcripts, so this is
the only place it can be done). The client uploads nothing. After
`call_ended` it reads the result from `GET /api/v1/calls/<call_id>`, which
may take a few seconds to be ready. The mock does not produce results.

## Not decided yet

- How `POST /api/v1/calls` hands out the call ID and token.
- How the pipeline hands call results to the backend, and how the client
  knows they are ready.
- Whether caller audio moves from WAV-per-message to raw binary PCM later
  (smaller, and playback can start sooner).
- Interruption.

AI-assisted documentation (written with Claude Code), cited per the CSC398 AI
policy.
