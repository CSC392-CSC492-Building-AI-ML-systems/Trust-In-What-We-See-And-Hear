# Testing the voice pipeline on a GPU pod

The pipeline needs a large GPU, so we rent one from RunPod by the hour.
`pod.py` rents it, sets it up and gives you a link for test calls in the
browser. It works on Windows, macOS and Linux.

A pod costs about $0.60 to $1.10 an hour on the team's account, and setup
takes 5 to 25 minutes, depending on the machine RunPod gives you. It stops itself after 30 minutes without a call, but
run `stop` when you are done.

## Before the first time

1. Get your API key from me (Uyiosa). Keep it private.
2. Install Python 3.9 or newer (`py --version` on Windows, `python3 --version`
   on macOS).
3. Clone the repo.
4. Record a WAV file of your voice, at least 5 seconds long. The caller
   clones it.

The first run asks for the key and can save it for next time.

## Commands

Run these from the repo's top folder. On Windows use `py` and backslashes.

| Command | What it does |
|---|---|
| `python3 voice-pipeline/dev/pod.py start --voice path/to/voice.wav` | Rents a GPU, sets up and prints the call link |
| `python3 voice-pipeline/dev/pod.py status` | Shows whether a pod is running, its cost, setup progress and link |
| `python3 voice-pipeline/dev/pod.py logs` | Shows what the pod has been doing |
| `python3 voice-pipeline/dev/pod.py stop` | Terminates the pod so it stops billing |

You can close the window during setup and check on it later with `status`.

## Making a call

Open the link. To hear the caller in your own voice, record 10 to 20
seconds on the page first; otherwise it uses the `--voice` file. Press Call,
then Answer, and talk. Use headphones so the
caller does not hear itself. The score and feedback appear after you hang
up. The link may show a 404 for the first minute.

Anyone with the link can make calls, so only share it in the team channel.

## Sharing the pod

The team shares one pod. If one is running, `start` gives you its link
instead of renting another. A call uses the voice recorded on the test
page, or else the `--voice` file of whoever started the pod.

## Options for `start`

| Option | Meaning |
|---|---|
| `--engine chatterbox` | Use the Chatterbox voice instead of Ming |
| `--max-price 1.5` | Highest hourly price to accept (default 2.50) |
| `--gpu L40S` | Only rent this GPU type (default: the cheapest that fits) |
| `--community` | Also try RunPod's cheaper, less reliable community cloud |
| `--idle-minutes 60` | Minutes without a call before it stops itself (default 30) |
| `--backend https://...` | Take calls from a backend instead of the test page (see below) |

## With the backend

`start --backend` runs the pod the way the app will use it: the backend
hands out calls, the pod claims them, downloads the persona's voice from
Supabase Storage and reports the score back. There is no test page and no
`--voice`. Calls start from the app, or from `backend_call.py` below.

The pod is on RunPod's network, so it can't reach `localhost`. For a
backend on your computer, `tunnels.py` gives it two public addresses
through Cloudflare quick tunnels (free, no account; install `cloudflared`
with `brew install cloudflared` or `winget install Cloudflare.cloudflared`).
One goes to the backend. The other serves only the signed voice links from
the local Supabase, not the rest of its API.

1. Start the local Supabase and the backend (see the root README).
2. In a second window, leave this running:
   `python3 voice-pipeline/dev/tunnels.py`
   With the hosted Supabase, add `--no-supabase`.
3. `python3 voice-pipeline/dev/pod.py start --backend <backend address it printed>`
4. When the pod is ready, restart the backend with the three settings the
   two scripts printed:

   | Setting | Value |
   |---|---|
   | `PIPELINE_WS_URL` | `wss://<pod id>-8001.proxy.runpod.net` |
   | `PIPELINE_BACKEND_SECRET` | the contents of `~/.tiwwsah/pipeline-backend-secret.txt`, which `pod.py` makes the first time |
   | `PIPELINE_SUPABASE_URL` | the voice-link address from `tunnels.py` (leave it out with `--no-supabase`) |

5. Make a call. Until the app can, from `backend/`:

   ```bash
   say -o r1.wav --data-format=LEI16@16000 "Hello? Who is this?"
   uv run python ../voice-pipeline/dev/backend_call.py \
       --email alice@example.com --password demo-password r1.wav
   ```

   It starts a call, plays each WAV as your side of the conversation,
   hangs up and prints the score the backend stored. The persona needs a
   voice uploaded through `PUT /api/v1/users/me/voice` (the local demo
   accounts have none), and each call uses up one consent.

The tunnel addresses change every time `tunnels.py` starts, and a pod keeps
the backend address it was started with, so restart both together.

## Frontend testing

Use the pod ID and token from the link:

- WebSocket: `wss://<pod id>-8001.proxy.runpod.net/ws/calls/<any call id>?token=<token>`
- Result: `https://<pod id>-8001.proxy.runpod.net/api/v1/calls/<call id>?token=<token>`

The messages are in `../PROTOCOL.md`.

## Problems

| Message | Fix |
|---|---|
| "Every GPU that fits is taken right now" | Try again in a few minutes, or add `--community` |
| "`ssh` is not installed" (Windows) | Settings, System, Optional features, add OpenSSH Client |
| "is not really a WAV file inside" | Convert it: `afconvert -f WAVE -d LEI16@24000 -c 1 in.m4a voice.wav` (macOS) or `ffmpeg -i in.m4a -ar 24000 -ac 1 voice.wav` (Windows) |
| "unrecognized arguments" | Put quotes around a path with spaces |
| "Python could not check RunPod's HTTPS certificate" (Mac) | Run `Install Certificates.command` in the Python folder under Applications |
| "RunPod refused the API key (401)" | Ask me for a new key, then run `pod.py --new-key status` |
| Anything else | Send the output to the team chat |

Coding agents: only start or stop pods when the person asks, since each
start costs money.

AI-assisted documentation (written with Claude Code), cited per the CSC398
AI policy.
