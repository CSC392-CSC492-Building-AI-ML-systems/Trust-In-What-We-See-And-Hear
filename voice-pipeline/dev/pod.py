#!/usr/bin/env python3
"""Start, check and stop the team's GPU pod for the voice pipeline.

Works on Windows, macOS and Linux with Python 3.9 or newer. It needs no extra
packages, no SSH key and no folders set up beforehand. Run it from the
repository's top folder:

    python voice-pipeline/dev/pod.py start     rent a GPU and set everything up
    python voice-pipeline/dev/pod.py status    is a pod running? its link, cost, progress
    python voice-pipeline/dev/pod.py logs      what the pod has been doing
    python voice-pipeline/dev/pod.py stop      terminate the pod (billing stops)

On Windows, use `py` in place of `python` if `python` is not found.
See voice-pipeline/dev/POD.md for the guide. `start --help` lists the options.

It talks to RunPod's API (https://api.runpod.io/v2) with your RunPod API key,
which it asks for the first time and keeps in a folder it makes in your home
folder (.tiwwsah). Code and commands reach the pod through the same API, so
no SSH is involved.

AI-assisted code (written with Claude Code), cited per the CSC398 AI policy.
"""

from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import io
import json
import os
import secrets
import sys
import tarfile
import time
import urllib.error
import urllib.request
from pathlib import Path

API = "https://api.runpod.io/v2"
REPO = Path(__file__).resolve().parents[2]
STATE = Path.home() / ".tiwwsah"
KEY_FILE = STATE / "runpod-api-key.txt"
POD_NAME = "voice-pipeline-test"  # every pod this script makes has this name
IMAGE = "runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404"
MIN_CUDA = "12.8"  # the image needs a host with CUDA 12.8 or newer
UPLOAD = ["voice-pipeline", "backend/app"]  # the pipeline reads backend/app's call context rules
SKIP_DIRS = {".venv", "venv", "__pycache__", ".pytest_cache", "node_modules", ".mypy_cache"}

# What each voice engine needs: GPU memory (GB) and container disk (GB).
ENGINES = {
    "chatterbox": (48, 70),
    "ming": (48, 80),
    "drama": (80, 150),
}
LLM_FOR_CARD = "Qwen/Qwen3-14B-FP8"
LLM_GB = 29  # the share of the GPU the LLM gets, in GB (VLLM_UTIL = this / card)
# FP8 models fail on these (setup.sh gives them a full-precision model instead).
NO_FP8 = ("RTX 50", "RTX PRO", "B200", "B300")


class PodError(Exception):
    """Something went wrong that the user should read about; no traceback."""


def say(text: str = "") -> None:
    print(text, flush=True)


def ask_yes(question: str, default: bool = False) -> bool:
    hint = "[Y/n]" if default else "[y/N]"
    try:
        answer = input(f"{question} {hint} ").strip().lower()
    except EOFError:
        return default
    return default if not answer else answer in ("y", "yes")


# ---------------------------------------------------------------- RunPod API

class RunPod:
    def __init__(self, key: str):
        self.key = key

    def call(self, method: str, path: str, body: dict | None = None,
             timeout: float = 60) -> tuple[int, object]:
        """One API request. Returns (status, parsed body). Retries on rate limits."""
        data = json.dumps(body).encode() if body is not None else None
        for attempt in range(6):
            request = urllib.request.Request(API + path, data=data, method=method, headers={
                "Authorization": f"Bearer {self.key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "voice-pipeline-pod-script",
            })
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    raw = response.read()
                    return response.status, json.loads(raw) if raw else None
            except urllib.error.HTTPError as e:
                raw = e.read()
                try:
                    parsed = json.loads(raw) if raw else None
                except ValueError:
                    parsed = {"detail": raw.decode(errors="replace")[:300]}
                if e.code == 429 and attempt < 5:
                    time.sleep(float(e.headers.get("Retry-After") or 5))
                    continue
                if e.code == 401:
                    raise PodError("RunPod refused the API key (401). Run this again with --new-key "
                                   "before the command (pod.py --new-key start) and paste a key made "
                                   "in the team account.")
                return e.code, parsed
            except urllib.error.URLError as e:
                if "CERTIFICATE_VERIFY_FAILED" in str(e.reason):
                    raise PodError(
                        "Python could not check RunPod's HTTPS certificate. On a Mac with Python "
                        "from python.org, run 'Install Certificates.command' in the Python folder "
                        "under Applications, then try again.")
                if attempt < 2:
                    time.sleep(3)
                    continue
                raise PodError(f"Could not reach RunPod ({e.reason}). Check your internet connection.")
        raise PodError("RunPod kept saying 'too many requests'. Wait a minute and try again.")

    def must(self, method: str, path: str, body: dict | None = None, what: str = "") -> object:
        status, parsed = self.call(method, path, body)
        if status >= 300:
            raise PodError(f"RunPod could not {what or path} (HTTP {status}): {detail(parsed)}")
        return parsed

    def our_pods(self) -> list[dict]:
        parsed = self.must("GET", "/pods", what="list the pods")
        pods = parsed.get("pods", parsed.get("data", [])) if isinstance(parsed, dict) else parsed
        return [p for p in pods or [] if str(p.get("name", "")).startswith(POD_NAME)]

    def pod(self, pod_id: str) -> dict | None:
        status, parsed = self.call("GET", f"/pods/{pod_id}")
        if status == 404:
            return None
        if status >= 300:
            raise PodError(f"RunPod could not read pod {pod_id} (HTTP {status}): {detail(parsed)}")
        return parsed

    def run(self, pod_id: str, command: list[str], env: dict | None = None,
            timeout: int = 20) -> dict:
        """Run one command on the pod and return stdout, stderr and exitCode."""
        body: dict = {"command": command, "timeoutSeconds": timeout, "cwd": "/root"}
        if env:
            body["env"] = env
        for _ in range(40):
            status, parsed = self.call("POST", f"/pods/{pod_id}/exec", body, timeout=timeout + 30)
            if status == 200:
                return parsed
            if status in (409, 503):  # the container is still starting
                time.sleep(5)
                continue
            if status == 403:
                raise PodError(f"This API key is not allowed to run commands on pods. {KEY_HELP}")
            raise PodError(f"RunPod could not run a command on the pod (HTTP {status}): {detail(parsed)}")
        raise PodError("The pod did not accept commands for over 3 minutes.")

    def shell(self, pod_id: str, script: str, env: dict | None = None, timeout: int = 20) -> str:
        result = self.run(pod_id, ["bash", "-c", script], env, timeout)
        if result.get("exitCode") not in (0, None):
            raise PodError(f"A command on the pod failed: {(result.get('stderr') or '').strip()[-500:]}")
        return result.get("stdout") or ""


def detail(parsed: object) -> str:
    if isinstance(parsed, dict):
        text = parsed.get("detail") or parsed.get("title") or parsed.get("error") or json.dumps(parsed)
        errors = parsed.get("errors")
        if errors:
            text += " " + json.dumps(errors)[:400]
        return str(text)
    return str(parsed)[:400]


# ---------------------------------------------------------------- the API key

def load_key(new_key: bool) -> str:
    if os.environ.get("RUNPOD_API_KEY") and not new_key:
        return os.environ["RUNPOD_API_KEY"].strip()
    if KEY_FILE.is_file() and not new_key:
        return KEY_FILE.read_text().strip()
    say("This needs the RunPod team's API key. Ask the team admin for it: Developer")
    say("members cannot make one. (The admin makes it with the team picked: Credentials,")
    say("API Keys, Create API Key.) Paste it here; it is not shown as you paste.")
    key = getpass.getpass("RunPod API key: ").strip()
    if not key:
        raise PodError("No key given.")
    account = RunPod(key).must("GET", "/account", what="check the key")
    say(f"The key works. It belongs to the RunPod account of {account.get('email', 'unknown')}.")
    if ask_yes(f"Keep the key on this computer ({KEY_FILE}) so you are not asked again?", True):
        STATE.mkdir(parents=True, exist_ok=True)
        KEY_FILE.write_text(key + "\n")
        try:
            os.chmod(KEY_FILE, 0o600)  # only you can read it (no effect on Windows)
        except OSError:
            pass
    return key


# ---------------------------------------------------------------- choosing a GPU

def candidates(api: RunPod, need_gb: int, max_price: float, community: bool) -> list[dict]:
    """GPU types that fit, those in stock first, then by price."""
    rank = {"HIGH": 0, "MEDIUM": 1, "LOW": 2, "NONE": 3}
    clouds = ["SECURE"] + (["COMMUNITY"] if community else [])
    found = []
    for cloud in clouds:
        parsed = api.must("GET", "/catalog/gpus?include=AVAILABILITY&product=POD"
                          f"&minCudaVersion={MIN_CUDA}&cloud={cloud}", what="read the GPU list")
        for gpu in parsed.get("gpus", []):
            price = (gpu.get("price") or {}).get(cloud.lower()) or 0
            if (gpu.get("manufacturer") != "NVIDIA" or "MIG" in gpu["id"]
                    or not gpu.get(cloud.lower()) or gpu.get("memory", 0) < need_gb
                    or not 0 < price <= max_price):
                continue
            found.append({"id": gpu["id"], "name": gpu.get("name", gpu["id"]), "cloud": cloud,
                          "memory": gpu["memory"], "price": price,
                          "stock": gpu.get("availability", "NONE")})
    found.sort(key=lambda g: (rank.get(g["stock"], 3), g["cloud"] != "SECURE", g["price"]))
    return found


# ---------------------------------------------------------------- start

def make_archive() -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for top in UPLOAD:
            base = REPO / top
            if not base.is_dir():
                raise PodError(f"{base} is missing. Run this from a full copy of the repository.")
            for root, dirs, files in os.walk(base):
                dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
                for name in sorted(files):
                    path = Path(root) / name
                    # Forward slashes in the archive, also on Windows.
                    tar.add(str(path), arcname=path.relative_to(REPO).as_posix())
    return buffer.getvalue()


def upload(api: RunPod, pod_id: str, data: bytes, target: str) -> None:
    """Send a file through the command API: base64 pieces, then checked."""
    text = base64.b64encode(data).decode()
    pieces = [text[i:i + 4000] for i in range(0, len(text), 4000)]
    api.shell(pod_id, f"rm -f {target}.b64")
    for start in range(0, len(pieces), 60):
        batch = pieces[start:start + 60]
        api.run(pod_id, ["bash", "-c", f'printf %s "$@" >> {target}.b64', "upload", *batch])
    want = hashlib.sha256(data).hexdigest()
    got = api.shell(pod_id, f"base64 -d < {target}.b64 > {target} && rm {target}.b64 "
                            f"&& sha256sum {target}").split()[:1]
    if got != [want]:
        raise PodError("The upload to the pod came out damaged. Run start again.")


def create_pod(api: RunPod, args: argparse.Namespace, token: str) -> tuple[dict, dict]:
    need_gb, disk = ENGINES[args.engine]
    options = candidates(api, need_gb, args.max_price, args.community)
    if not options:
        raise PodError(f"No GPU with {need_gb} GB or more costs under ${args.max_price:.2f}/h. "
                       "Raise --max-price or try again later.")
    in_stock = [g for g in options if g["stock"] != "NONE"]
    say(f"GPUs that fit ({need_gb} GB or more, under ${args.max_price:.2f}/h), best first:")
    for g in options[:8]:
        say(f"  {g['name']:<16} {g['memory']:>3} GB  {g['cloud'].lower():<9} "
            f"${g['price']:.2f}/h  stock: {g['stock'].lower()}")
    if not in_stock:
        say("RunPod lists none of them as in stock right now. Trying anyway, since stock changes "
            "by the minute.")
    first = options[0]
    say()
    say(f"The pod bills from the moment it is created until it is stopped: about "
        f"${first['price']:.2f} per hour on a {first['name']} (more if that one is taken).")
    say(f"It stops by itself after {args.idle_minutes} minutes without a call."
        if args.idle_minutes else "Automatic shutdown is OFF: remember to run stop.")
    if not args.yes and not ask_yes("Start a pod?"):
        raise PodError("Nothing was started.")

    env = {"PIPELINE_DEV_TOKEN": token, "TTS_ENGINE": args.engine,
           "IDLE_MINUTES": str(args.idle_minutes)}
    shared = env
    for gpu in options:
        env = dict(shared)
        if not any(mark in gpu["name"] or mark in gpu["id"] for mark in NO_FP8):
            env["LLM_MODEL"] = args.llm or LLM_FOR_CARD
        elif args.llm:
            env["LLM_MODEL"] = args.llm
        # else setup.sh picks a model that runs on that card
        env["VLLM_UTIL"] = f"{min(0.85, LLM_GB / gpu['memory']):.2f}"
        body = {"name": pod_name(), "image": IMAGE,
                "gpu": {"id": gpu["id"], "count": 1, "minCudaVersion": MIN_CUDA},
                "cloud": gpu["cloud"], "disk": disk, "ports": ["8001/http"], "env": env}
        say(f"Asking for a {gpu['name']} ({gpu['cloud'].lower()} cloud)...")
        status, parsed = api.call("POST", "/pods", body)
        if status in (200, 201, 202) and isinstance(parsed, dict) and parsed.get("id"):
            say(f"Got one. Pod ID: {parsed['id']}")
            return parsed, env
        if status in (400, 409):
            say(f"  not available: {detail(parsed)[:150]}")
            continue
        raise PodError(f"RunPod refused to create the pod (HTTP {status}): {detail(parsed)}")
    raise PodError("Every GPU that fits is taken right now. Try again in a few minutes, add "
                   "--community for the cheaper community cloud, or raise --max-price.")


KEY_HELP = ("The team admin can fix the key: in the RunPod console, Credentials, API Keys, "
            "the pencil icon on the key, then allow running commands on pods (or give the key "
            "All access).")


def check_key_can_run_commands(api: RunPod) -> None:
    """Fail before renting if the key may not run commands on pods.

    Asks to run a command on a pod that does not exist: a key without the
    permission is refused (403) before RunPod looks for the pod (404).
    """
    status, parsed = api.call("POST", "/pods/no-such-pod-permission-check/exec",
                              {"command": ["true"], "timeoutSeconds": 1})
    if status == 403:
        raise PodError(f"This API key is not allowed to run commands on pods, which the setup "
                       f"needs. Nothing was rented. {KEY_HELP}")


def earlier_pod(api: RunPod, mine: str) -> dict | None:
    """Another of our pods created before this one, if two starts raced.

    Both starts run this check after creating, and only the later pod (by
    creation time, then ID) finds an earlier one, so exactly one pod stays.
    """
    me = api.pod(mine) or {"id": mine, "createdAt": ""}
    key = lambda p: (str(p.get("createdAt") or ""), p["id"])  # noqa: E731
    others = [p for p in api.our_pods()
              if p["id"] != mine and p.get("status") != "TERMINATED" and key(p) < key(me)]
    return min(others, key=key) if others else None


def wait_running(api: RunPod, pod_id: str) -> dict:
    started = time.time()
    shown = ""
    while time.time() - started < 900:
        pod = api.pod(pod_id)
        if pod is None:
            raise PodError("The pod disappeared while starting (someone may have stopped it).")
        status = pod.get("status", "")
        if status != shown:
            say(f"  pod status: {status.lower()} ({int(time.time() - started)} s)")
            shown = status
        if status == "RUNNING":
            return pod
        if status in ("ERROR", "EXITED", "TERMINATED"):
            raise PodError(f"The pod ended up {status.lower()} instead of running.")
        time.sleep(5)
    raise PodError("The pod took over 15 minutes to boot.")


def follow(api: RunPod, pod_id: str) -> str:
    """Print the pod's progress until it is ready or failed. Returns the stage."""
    shown = 0
    began = quiet_since = time.time()
    while True:
        out = api.shell(pod_id, "cat /root/stage 2>/dev/null; echo; cat /root/session.log 2>/dev/null")
        stage, _, log = out.partition("\n")
        lines = log.splitlines()
        for line in lines[shown:]:
            say("  " + line)
            quiet_since = time.time()
        shown = max(shown, len(lines))
        stage = stage.strip()
        if stage in ("ready", "failed"):
            return stage
        if not stage and time.time() - began > 120:
            raise PodError("The setup on the pod did not start. See: python voice-pipeline/dev/pod.py logs")
        if time.time() - quiet_since > 90:
            say("  (still working; nothing new to report yet)")
            quiet_since = time.time()
        time.sleep(10)


def pod_name() -> str:
    """voice-pipeline-test-<who started it>, so the team can tell whose pod it is."""
    try:
        who = getpass.getuser()
    except Exception:
        who = "someone"
    who = "".join(c for c in who.lower() if c.isalnum())[:20] or "someone"
    return f"{POD_NAME}-{who}"


def call_link(pod_id: str, token: str) -> str:
    return f"https://{pod_id}-8001.proxy.runpod.net/?token={token}"


def show_ready(pod: dict, token: str) -> None:
    say()
    say("=" * 70)
    say("The voice pipeline is running. Make a call here:")
    say()
    say(f"  {call_link(pod['id'], token)}")
    say()
    say(f"  Pod: {pod['id']}   cost: ${float(pod.get('cost') or 0):.2f}/h")
    say("  Use headphones. Share these links only with the team: anyone with them can call.")
    say("  The links can answer 404 for the first minute.")
    say("  When you are done: python voice-pipeline/dev/pod.py stop")
    say("=" * 70)


def cmd_start(api: RunPod, args: argparse.Namespace) -> None:
    voice = Path(args.voice)
    if not voice.is_file():
        raise PodError(f"{voice} does not exist.")
    if voice.read_bytes()[:4] != b"RIFF":
        raise PodError(
            f"{voice} is not really a WAV file inside (renaming a recording to .wav does not "
            "convert it). Convert it first.\n"
            f'  macOS:   afconvert -f WAVE -d LEI16@24000 -c 1 "{voice}" voice.wav\n'
            f'  Windows: ffmpeg -i "{voice}" -ar 24000 -ac 1 voice.wav   (ffmpeg: winget install ffmpeg)\n'
            "Or record in Audacity and use File, Export, Export as WAV.")
    existing = [p for p in api.our_pods() if p.get("status") not in ("TERMINATED",)]
    if existing and not args.another:
        say("A pod is already running for the team, so none was started:")
        for pod in existing:
            show_pod(api, pod)
        say("Use that one, or stop it first. (--another starts a second one, at a second price.)")
        return

    check_key_can_run_commands(api)
    token = secrets.token_urlsafe(12)
    pod, env = create_pod(api, args, token)
    pod_id = pod["id"]
    if not args.another:
        first = earlier_pod(api, pod_id)
        if first is not None:
            say("A teammate started a pod at the same moment. Keeping theirs and removing this one.")
            terminate(api, pod_id)
            show_pod(api, first)
            return
    try:
        say("Waiting for the pod to boot. This usually takes 1 to 3 minutes.")
        pod = wait_running(api, pod_id)
        say("Waiting for the pod to accept commands...")
        api.shell(pod_id, "mkdir -p /root/app")
        say("Uploading the code...")
        upload(api, pod_id, make_archive(), "/root/app.tar.gz")
        api.shell(pod_id, "tar -xzf /root/app.tar.gz -C /root/app && rm /root/app.tar.gz")
        say(f"Uploading the voice recording {voice.name}...")
        upload(api, pod_id, voice.read_bytes(), "/root/voice.wav")
        say("Starting the setup on the pod. The whole thing takes 10 to 20 minutes;")
        say("progress is shown below. You can close this window at any time: the setup")
        say("carries on, and `status` shows where it is.")
        say()
        api.shell(pod_id, "cd /root/app && setsid nohup bash voice-pipeline/dev/session.sh "
                          "> /root/session.log 2>&1 < /dev/null & echo started", env=env)
        stage = follow(api, pod_id)
    except KeyboardInterrupt:
        say()
        say(f"Stopped watching. Pod {pod_id} keeps going and billing.")
        say("  python voice-pipeline/dev/pod.py status   shows how it is doing")
        say("  python voice-pipeline/dev/pod.py stop     terminates it")
        return
    except PodError:
        say(f"Something went wrong after the pod was created. Pod {pod_id} is still running.")
        if args.yes or ask_yes("Terminate it now so it stops billing?", True):
            terminate(api, pod_id)
        raise
    if stage == "ready":
        show_ready(api.pod(pod_id) or pod, token)
    else:
        say()
        say("The setup failed; the reason is above.")
        if args.yes or ask_yes("Terminate the pod now so it stops billing?", True):
            terminate(api, pod_id)
        else:
            say(f"It shuts itself down in {args.idle_minutes} minutes.")


# ---------------------------------------------------------------- status, logs, stop

def show_pod(api: RunPod, pod: dict) -> None:
    pod = api.pod(pod["id"]) or pod
    env = pod.get("env") or {}
    gpu = (pod.get("gpu") or {}).get("id", "?")
    say(f"Pod {pod['id']} ({pod.get('name')}): {str(pod.get('status')).lower()}, {gpu}, "
        f"${float(pod.get('cost') or 0):.2f}/h, created {pod.get('createdAt', '?')}")
    if pod.get("status") != "RUNNING":
        return
    try:
        out = api.shell(pod["id"], "cat /root/stage 2>/dev/null; echo; tail -n 3 /root/session.log 2>/dev/null")
    except PodError as e:
        say(f"  (could not ask the pod how it is doing: {e})")
        return
    stage, _, last = out.partition("\n")
    stage = stage.strip() or "not started"
    say(f"  setup: {stage}")
    for line in last.splitlines():
        say("    " + line)
    token = env.get("PIPELINE_DEV_TOKEN")
    if stage == "ready" and token:
        say(f"  make a call: {call_link(pod['id'], token)}")


def cmd_status(api: RunPod, args: argparse.Namespace) -> None:
    pods = api.our_pods()
    if not pods:
        say("No voice pipeline pod is running. Nothing is billing for it.")
        return
    for pod in pods:
        show_pod(api, pod)


def cmd_logs(api: RunPod, args: argparse.Namespace) -> None:
    pods = [p for p in api.our_pods() if p.get("status") == "RUNNING"]
    if not pods:
        say("No running pod.")
        return
    files = ["/root/session.log"] + (["/root/setup.log", "/root/vllm.log", "/root/server.log"]
                                     if args.all else [])
    for pod in pods:
        for name in files:
            say(f"===== {pod['id']} {name} (last {args.lines} lines)")
            say(api.shell(pod["id"], f"tail -n {args.lines} {name} 2>/dev/null || echo '(none yet)'"))


def terminate(api: RunPod, pod_id: str) -> None:
    status, parsed = api.call("DELETE", f"/pods/{pod_id}")
    if status >= 300 and status != 404:
        raise PodError(f"RunPod could not terminate pod {pod_id} (HTTP {status}): {detail(parsed)}. "
                       "Terminate it in the console: https://www.console.runpod.io/pods")
    for _ in range(12):
        pod = api.pod(pod_id)
        if pod is None or pod.get("status") == "TERMINATED":
            say(f"Pod {pod_id} is terminated. It no longer bills.")
            return
        time.sleep(5)
    say(f"Pod {pod_id} still shows as {pod.get('status')}. Check https://www.console.runpod.io/pods")


def cmd_stop(api: RunPod, args: argparse.Namespace) -> None:
    pods = [p for p in api.our_pods() if p.get("status") != "TERMINATED"]
    if not pods:
        say("No voice pipeline pod is running. Nothing to stop.")
        return
    for pod in pods:
        say(f"Pod {pod['id']} ({pod.get('name')}), {str(pod.get('status')).lower()}.")
        say("Terminating deletes it and everything on it; the next start sets up from scratch.")
        say("If teammates are using it, their calls end.")
        if args.yes or ask_yes("Terminate it?"):
            terminate(api, pod["id"])


# ---------------------------------------------------------------- main

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Start, check and stop the team's GPU pod for the voice pipeline. "
                    "Guide: voice-pipeline/dev/POD.md")
    parser.add_argument("--new-key", action="store_true", help="ask for a new RunPod API key")
    sub = parser.add_subparsers(dest="command", required=True)

    start = sub.add_parser("start", help="rent a GPU and set up the voice pipeline on it")
    start.add_argument("--engine", choices=sorted(ENGINES), default="chatterbox",
                       help="voice engine (default chatterbox; drama needs an 80 GB GPU)")
    # Until the pod runs with the backend (PR #15), it has no other way to
    # get the voice: the app's recordings live in the backend's storage.
    start.add_argument("--voice", required=True,
                       help="WAV recording of the voice to clone, at least 5 seconds (required)")
    start.add_argument("--llm", help=f"LLM to serve (default {LLM_FOR_CARD})")
    start.add_argument("--max-price", type=float, default=2.5,
                       help="highest price per hour to accept, in USD (default 2.50)")
    start.add_argument("--community", action="store_true",
                       help="also try RunPod's community cloud (cheaper, less reliable)")
    start.add_argument("--idle-minutes", type=int, default=30,
                       help="terminate the pod after this long without a call (default 30, 0 = never)")
    start.add_argument("--another", action="store_true", help="start even if a pod is already running")
    start.add_argument("--yes", action="store_true", help="do not ask before starting or stopping")

    sub.add_parser("status", help="show the team's pod, its progress and its links")
    logs = sub.add_parser("logs", help="show what the pod has been doing")
    logs.add_argument("--all", action="store_true", help="also the install, LLM and server logs")
    logs.add_argument("--lines", type=int, default=60)
    stop = sub.add_parser("stop", help="terminate the pod so it stops billing")
    stop.add_argument("--yes", action="store_true", help="do not ask first")

    args = parser.parse_args()
    try:
        api = RunPod(load_key(args.new_key))
        {"start": cmd_start, "status": cmd_status, "logs": cmd_logs, "stop": cmd_stop}[args.command](api, args)
    except PodError as e:
        say()
        say(f"Error: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        say()
        say("Interrupted.")
        sys.exit(130)


if __name__ == "__main__":
    main()
