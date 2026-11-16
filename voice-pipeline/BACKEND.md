# What the pipeline needs from the backend (proposal)

How a call gets from `POST /api/v1/calls` to the voice pipeline and back.
The pipeline side of "Claiming a call" is built (`pipeline/calls.py`,
`BackendCalls`). The backend side is not. Names follow
`supabase/database-schema.md` where it already has them.

The pipeline never reads the database or Storage itself. It holds one secret
(for the backend) and no Supabase keys.

```
browser                    backend                      pipeline
   | POST /api/v1/calls       |                            |
   |------------------------->| checks consent, makes      |
   |   id, ws_url (has token) | the call row and a token   |
   |<-------------------------|                            |
   | connect to ws_url                                     |
   |------------------------------------------------------>|
   |                          |  POST .../claim (token)    |
   |                          |<---------------------------|
   |                          |  context, voice_url, limit |
   |                          |--------------------------->|
   |                  the call (PROTOCOL.md)               |
   |<----------------------------------------------------->|
   |                          |  POST .../result           |
   |                          |<---------------------------|
   | GET /api/v1/calls/<id>   |                            |
   |------------------------->|                            |
```

## 1. Starting a call: `POST /api/v1/calls`

Already specified: the logged-in user sends `consent_id` and `scenario_id`.
The backend runs `use_consent`, creates the `calls` row as `queued`, and
makes a token. The response gains one field:

```json
{
  "id": "<call id>",
  "consent_id": "...",
  "scenario_id": "...",
  "status": "queued",
  "ws_url": "wss://<pipeline host>/ws/calls/<call id>?token=<token>"
}
```

- `ws_url` is complete, so the frontend does not need to know where the
  pipeline runs. The host is a backend setting.
- The token is random (32 bytes or more, URL-safe), single use, and expires
  if not claimed within 2 minutes. Store only a hash of it.

## 2. Claiming a call: `POST /api/v1/internal/calls/<call id>/claim`

Called by the pipeline when the browser connects.

```
Authorization: Bearer <PIPELINE_BACKEND_SECRET>
{"token": "<token from the browser>"}
```

The backend checks the token against the call, marks it used, sets the call
to `in_progress` with `started_at`, and answers:

```json
{
  "context": { "persona": {}, "target": {}, "scenario": {}, "details": {} },
  "voice_url": "https://<storage>/...signed...",
  "max_duration_seconds": 300
}
```

- `context`: the call context from `backend/app/modules/calls/context.py`
  (example: `sample_call_context.json`).
- `voice_url`: a signed Supabase Storage link to the persona's
  `voices.sample_storage_path`, valid for at least 60 seconds. It must be
  `https` and a WAV of at most 25 MB (the upload endpoint already makes mono
  WAV). The pipeline downloads it once, deletes its copy when the voice is
  ready, and keeps nothing after the call.
- `max_duration_seconds`: from the scenario. It must be more than 0, or the
  call fails to set up. The pipeline caps it at 600.

Answers the pipeline understands:

| Status | Meaning | The browser sees |
|---|---|---|
| 200 | the call may start | `ready` |
| 403 | token wrong, expired or already used | socket closed, code `4401` |
| 404 | no such call | socket closed, code `4401` |
| anything else, or no answer in 10 s | backend problem | `error` `setup_failed`, then `call_ended` |

If the pipeline has no free line it says `busy` without claiming, so the
token is not spent and the same `ws_url` can be tried again until it expires.

A call that was claimed but never gets a result (the pod died, for example)
stays `in_progress`. The backend should mark such calls `failed` after
`max_duration_seconds` plus a few minutes.

## 3. Handing over the result: `POST /api/v1/internal/calls/<call id>/result`

The pipeline side is built (`pipeline/scoring.py` and `BackendCalls.report`).
The backend side is not. Sent by the pipeline after every call that was
claimed, a few seconds after it ends, with the same `Authorization` header.
If the backend is down or answers 429 or 5xx, the pipeline tries twice more
over about five seconds and then gives up, so the endpoint should accept the
same result twice.

```json
{
  "ended_by": "hangup",
  "duration_seconds": 42,
  "result": {
    "outcome": "recognized_scam",
    "score": 85,
    "summary": "You questioned who was calling and did not agree to send money.",
    "did_well": ["Asked the caller to prove who they were."],
    "to_improve": ["Hang up and call the person back on a number you know."],
    "red_flags_noticed": ["urgency"],
    "red_flags_missed": ["secrecy"],
    "sensitive_info_shared": false,
    "model_version": "qwen3-8b/score-v1"
  }
}
```

- `ended_by`: the `call_ended` reason (`hangup`, `time_limit`, `completed`,
  `error`).
- `result` matches the `call_results` table column for column. It is `null`
  when the call could not be scored, or never started.
- A call where the user said nothing is `inconclusive` with a `score` of 0.
  The score means nothing for that outcome and should not be shown.
- `red_flags_noticed` and `red_flags_missed` only contain values from the
  scenario's `tactics`, when it has any.
- The backend stores the result, sets `ended_at` and `duration_seconds`, and
  sets the call to `completed`, or `failed` when `ended_by` is `error` or
  `result` is `null`.
- No transcript is sent or stored anywhere.

## 4. What the browser reads: `GET /api/v1/calls/<call id>`

`status` uses the existing `call_status` values. `summary` is `null` until
the call is `completed`, then it is the stored result plus
`duration_seconds`:

```json
{
  "id": "<call id>",
  "consent_id": "...",
  "scenario_id": "...",
  "status": "completed",
  "summary": {
    "outcome": "recognized_scam",
    "score": 85,
    "summary": "...",
    "did_well": [],
    "to_improve": [],
    "red_flags_noticed": [],
    "red_flags_missed": [],
    "sensitive_info_shared": false,
    "duration_seconds": 42
  }
}
```

After `call_ended` the browser polls this until `status` is `completed` or
`failed`. `mock_server.py` serves sections 1 and 4 with canned data, so the
frontend can build against them now.

## Settings

| Where | Setting | For |
|---|---|---|
| backend | pipeline WebSocket address | building `ws_url` |
| backend | pipeline secret | checking the `Authorization` header |
| pipeline | `PIPELINE_BACKEND_URL` | where to send claim and result |
| pipeline | `PIPELINE_BACKEND_SECRET` | the same secret |

The pipeline runs on a rented GPU machine, so the backend must be reachable
from the internet over `https` for this to work. Without
`PIPELINE_BACKEND_URL` the pipeline uses one fixed token and voice file
(`DevCalls`), which is how it is tested today.

## Still open

- `scenario.ask` has no column yet, and it is not decided whose onboarding
  answers go in `details` (both noted in `context.py`).
- Whether to add a `scoring` status between `in_progress` and `completed`.
  Not needed for the flow above.

AI-assisted documentation (written with Claude Code), cited per the CSC398 AI
policy.
