# Running the voice pipeline on a GPU pod (development only)

This is a tool for testing the pipeline during development. It is not how
the app reaches the pipeline: the frontend and backend connect to it as
described in `../PROTOCOL.md` and `../BACKEND.md`.

The real voice pipeline (speech to text, the LLM and the cloned voice) needs
a large GPU. We rent one from RunPod by the hour, on the team's RunPod
account. One script rents the GPU, sets everything up and gives you a link
you can open in a browser to make test calls. The same script stops it.

It works on Windows, macOS and Linux. You need Python and the `ssh` command,
which macOS, Linux and Windows 10 and 11 already have. You do not need to
make or register SSH keys: the script makes its own.

## What it costs

A pod bills from the moment it is created until it is stopped, whether or
not anyone uses it. With the default settings it costs about $0.70 to $1.10
an hour on a 48 GB card, and the script shows the price before it starts
anything. Setting up takes 10 to 20 minutes of that time.

The pod stops itself after 30 minutes without a call. Still run `stop` when
you are done.

## Before the first time

1. Get your API key from the team admin. Each person gets their own key,
   made on the team's account, so pods you start bill the team. The admin
   makes it with the team picked (Credentials, API Keys, Create API Key),
   restricted to REST API Pods read and write, Catalog read and Account
   read, and sends it privately. Keep it somewhere safe and never post it
   in a shared channel. If it leaks, tell the admin: they delete that key
   and make you a new one, and nobody else is affected.
2. You do not need to join the RunPod team. The key is all the script
   needs. The team only has a few seats, which give access to the RunPod
   website (pods, logs, billing). Without a seat, use `status`, `logs` and
   `stop` below, and ask someone with a seat if a pod will not stop.
3. Install Python 3.9 or newer.
   - Windows: `winget install Python.Python.3.12` in PowerShell, or the
     installer from python.org (tick "Add python.exe to PATH"). Check it with
     `py --version`.
   - macOS: `python3 --version`. If it is missing, install it from python.org
     or with `brew install python`.
4. Get the repository (`git clone`, then `cd` into it). Until this tool is
   merged into `main`, switch to its branch:
   `git checkout voice-pipeline-dev-tools`.
5. Have a voice recording ready: a WAV file of at least 5 seconds, of your
   own voice or of someone who agreed to it. The caller clones this voice.
   The voice samples the app stores are WAV files, so one recorded through
   the app can be downloaded from the backend's storage and used here.

For now the pod runs on its own, without the backend, which is why it needs
the recording passed in. The backend's call endpoints (PR #15) are merged,
but this script cannot start the pod with the backend yet. Once it can,
each call starts in the app and uses the voice recorded there.

The first time you run the script it asks for the API key and offers to
keep it in a `.tiwwsah` folder in your home folder. It makes that folder
itself.

## Starting the pod

From the repository's top folder:

| | Command |
|---|---|
| Windows (PowerShell) | `py voice-pipeline\dev\pod.py start --voice C:\path\to\voice.wav` |
| macOS or Linux | `python3 voice-pipeline/dev/pod.py start --voice path/to/voice.wav` |

It lists the GPUs that fit, with their prices and stock, and asks before
renting one. Then it shows each step as the pod works through it:

```
Got one. Pod ID: abc123xyz
Waiting for the pod to boot. This usually takes 1 to 3 minutes.
Uploading the code...
[14:02:10] Step 1 of 4: installing the Python packages. This takes about 5 minutes.
[14:02:40]   still installing: 2.1 GB of packages so far
[14:07:15] Step 2 of 4: downloading and loading the language model (Qwen/Qwen3-14B-FP8).
[14:07:45]   still loading the language model: 6.3 GB downloaded so far
[14:13:30] Step 3 of 4: installing and loading the ming voice engine. This takes 5 to 15 minutes.
[14:14:00]   still setting up the voice engine (12.4 GB on disk)
[14:19:40] Step 4 of 4: starting the call server. Loading the speech models takes 1 to 3 minutes.
[14:21:15] READY
```

At the end it prints the link to make calls.

You can close the window while it sets up. The setup carries on, and
`status` shows how far it got and, once it is ready, the links.

If a pod is already running for the team, `start` shows that pod's links
and does not rent a second one.

## Making a call

1. Open the link. Press Call, wait for "Incoming call", press Answer, and
   talk. Use headphones, or the caller hears itself.
2. After you hang up, the score and feedback appear under the conversation.

The link can answer 404 for the first minute. It needs no RunPod account:
anyone with the full link (the `token=` part included) can make calls while
the pod is up, so share it only in the team channel. Without the token the
page loads but every call is refused. If the link leaks, `stop` the pod; the
next pod gets a new token. Anyone with a key can print the link with
`status`.

## Checking on it and stopping it

| Command (macOS; on Windows use `py` and `\`) | What it does |
|---|---|
| `python3 voice-pipeline/dev/pod.py status` | Is a pod running, what it costs, how far the setup got, its links |
| `python3 voice-pipeline/dev/pod.py logs` | What the pod has been doing (`--all` adds the install, LLM and server logs) |
| `python3 voice-pipeline/dev/pod.py stop` | Terminates the pod, asking first for each one. Billing stops, and the next start sets up from scratch |

## When several people test at once

There is normally one pod for the team. If one is already up, `start` prints
its link instead of renting another, and if two people start at the same
moment the later pod removes itself. The pod takes 2 calls at a time; a
third caller hears that the lines are busy.

Every call uses the voice of whoever started the pod (the pod's name ends
with their computer's user name). To hear your own voice, take turns: when
the current tester is done, `stop` the pod and `start` it with your
recording. `stop` ends everyone's calls, so check with the team first.
`start --another` rents a second pod alongside the first, at a second price.

## Options for `start`

| Option | Meaning |
|---|---|
| `--voice voice.wav` | The voice to clone (required for now; see step 5 above) |
| `--engine chatterbox` | Voice engine (default `ming`, which sounded more like the speaker in testing). `drama` needs an 80 GB card and costs more |
| `--max-price 1.5` | Highest price per hour to accept (default 2.50) |
| `--community` | Also try RunPod's community cloud: cheaper and more often in stock, less reliable |
| `--idle-minutes 60` | Stop after this long without a call (default 30; 0 turns it off) |
| `--another` | Start a second pod even if one is running (costs a second hourly price) |
| `--yes` | Do not ask before renting |

## Pointing the app at the pod

The pod runs without the backend: every call uses the one token, the
`--voice` recording and the backend's sample scenario. To try the
frontend against it, use the pod's address and the token from the links:

- WebSocket: `wss://<pod id>-8001.proxy.runpod.net/ws/calls/<any call id>?token=<token>`
- Result: `https://<pod id>-8001.proxy.runpod.net/api/v1/calls/<call id>?token=<token>`

The messages are in `PROTOCOL.md`. The full app flow (the backend starting
each call) also needs this script to start the pod with the backend, which
it cannot do yet.

## When something goes wrong

| What you see | What to do |
|---|---|
| "Every GPU that fits is taken right now" | Stock changes by the minute. Try again, add `--community`, or raise `--max-price` |
| "`ssh` is not installed" (Windows) | Settings, System, Optional features, add OpenSSH Client |
| "Could not reach the pod over SSH" | Usually a slow start. Run `stop`, then `start` again; if it repeats, send the output to the team |
| "RunPod refused the API key (401)" | Your key was replaced or deleted, or was made without the team picked. Get a new one from the admin, then `pod.py --new-key status` and paste it |
| "Terminate it in the console" | `stop` failed. Try `stop` again; if it still fails and you have no seat, ask someone with one to terminate it on the RunPod website. It bills until then |
| "Python could not check RunPod's HTTPS certificate" (Mac) | Run `Install Certificates.command` in the Python folder under Applications |
| "is not really a WAV file inside" | The recording is another format renamed to .wav. Convert it: `afconvert -f WAVE -d LEI16@24000 -c 1 in.m4a voice.wav` (macOS) or `ffmpeg -i in.m4a -ar 24000 -ac 1 voice.wav` (Windows) |
| "unrecognized arguments" | A path with spaces needs quotes: `--voice "C:\My Files\voice.wav"` |
| `py` or `python3` is not found | Python is not installed or not on PATH; see step 3 above |
| "The setup failed" | The reason and the last log lines are printed above it. Send them to the team; the script offers to stop the pod |
| The page says the socket closed with code 4401 | The token in the address is wrong; copy the link from `status` again |
| The call does not start | The recording may be too short or not a WAV file. Run `logs --all` and look at the server log |

## For coding agents (Claude Code, Codex)

Start, check and stop pods only with this script, and only when the person
asks: every start rents a GPU on the team's money. Run `status` first, and
`stop` when the work is done. Pass `--yes` only if the person said to start
or stop without asking.

## How it works

`dev/pod.py` runs on your computer. It reads the GPU list and prices from
RunPod and rents the cheapest card in stock that fits, through RunPod's API
with your key. It then uploads the code and starts `dev/session.sh`
on the pod over SSH, with a key it made for itself in the `.tiwwsah` folder
in your home folder. Its public half is given to each pod when it is created.
(RunPod can run commands on a pod through its API too, but only for keys with
full access to the account, which we do not want to share.)

`dev/session.sh` runs on the pod. It calls `pod/setup.sh` (installs
everything, downloads and starts the LLM, and with `ming` or `drama` runs
`pod/voice_engine.sh`), then `pod/run.sh` (the call server, with the test page
`dev/call.html`). It writes its progress to `/root/session.log` and to the
pod's log in RunPod, which is what `status` and `logs` show to any teammate. It also terminates the pod once nobody has called
for the idle time, or when setup failed and nobody stopped the pod.

AI-assisted documentation (written with Claude Code), cited per the CSC398 AI
policy.
