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
| `--engine drama` | Use Fish Audio Drama 3, which streams its audio so the caller starts sooner. Needs an 80 GB card (about $2.10/h) |
| `--max-price 1.5` | Highest hourly price to accept (default 2.50) |
| `--gpu L40S` | Only rent this GPU type (default: the cheapest that fits) |
| `--community` | Also try RunPod's cheaper, less reliable community cloud |
| `--idle-minutes 60` | Minutes without a call before it stops itself (default 30) |

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
