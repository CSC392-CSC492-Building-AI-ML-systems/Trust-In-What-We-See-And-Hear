#!/usr/bin/env python3
"""Start, check and stop the team's GPU pod for the voice pipeline.

Works on Windows, macOS and Linux with Python 3.9 or newer. It needs no extra
packages and no folders or SSH keys set up beforehand. Run it from the
repository's top folder:

    python voice-pipeline/dev/pod.py start     rent a GPU and set everything up
    python voice-pipeline/dev/pod.py status    is a pod running? its link, cost, progress
    python voice-pipeline/dev/pod.py logs      what the pod has been doing
    python voice-pipeline/dev/pod.py stop      terminate the pod (billing stops)

On Windows, use `py` in place of `python` if `python` is not found.
See voice-pipeline/dev/POD.md for the guide. `start --help` lists the options.

It rents, checks and removes pods through RunPod's API
(https://api.runpod.io/v2) with the team's RunPod API key, which it asks for
the first time. The code reaches the pod over SSH, with a key the script
makes for itself; that way the team's RunPod key can be a restricted one.
(RunPod runs commands on a pod through its API only for keys with full
access to the account.) Everything it keeps is in a folder it makes in your
home folder (.tiwwsah).

AI-assisted code (written with Claude Code), cited per the CSC398 AI policy.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import io
import json
import os
import secrets
import shlex
import shutil
import subprocess
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
SSH_KEY = STATE / "pod-ssh-key"  # made by this script; its public half goes to each pod it starts
LOG_MARK = "[voice-pipeline] "  # session.sh's lines in the pod's container log
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

    def log_lines(self, pod_id: str, tail: int = 300, wait: float = 6) -> list[str] | str:
        """session.sh's progress lines from the pod's container log.

        Returns the lines, or a short reason if the log could not be read. The
        log is a stream that stays open: it can take several seconds to start,
        then it is read for `wait` seconds. Any key that can read pods can do
        this, not only the key of whoever started the pod.
        """
        request = urllib.request.Request(
            f"{API}/pods/{pod_id}/logs?source=container&tail={tail}",
            headers={"Authorization": f"Bearer {self.key}", "Accept": "text/event-stream",
                     "User-Agent": "voice-pipeline-pod-script"})
        lines: list[str] = []
        try:
            response = urllib.request.urlopen(request, timeout=30)
        except urllib.error.HTTPError as e:
            return f"RunPod answered HTTP {e.code}"
        except OSError as e:
            return f"could not open the log ({e})"
        with response:
            try:  # short reads from here on, so the open stream cannot hold us
                response.fp.raw._sock.settimeout(2)
            except AttributeError:
                pass
            deadline = time.time() + wait
            while time.time() < deadline:
                try:
                    raw = response.readline()
                except OSError:  # nothing more for now
                    break
                if not raw:
                    break
                text = raw.decode(errors="replace").strip()
                if text.startswith("data:"):
                    try:
                        line = json.loads(text[5:]).get("line", "")
                    except ValueError:
                        continue
                    if LOG_MARK in line:
                        lines.append(line.split(LOG_MARK, 1)[1])
        return lines


def detail(parsed: object) -> str:
    if isinstance(parsed, dict):
        text = parsed.get("detail") or parsed.get("title") or parsed.get("error") or json.dumps(parsed)
        errors = parsed.get("errors")
        if errors:
            text += " " + json.dumps(errors)[:400]
        return str(text)
    return str(parsed)[:400]


# ---------------------------------------------------------------- SSH

def ssh_key() -> Path:
    """This computer's key for reaching pods, made the first time it is needed."""
    for tool in ("ssh", "ssh-keygen"):
        if not shutil.which(tool):
            raise PodError(
                f"`{tool}` is not installed. On Windows 10 and 11 turn on OpenSSH Client in "
                "Settings, System, Optional features. On macOS and Linux it comes with the system.")
    if not SSH_KEY.is_file():
        STATE.mkdir(parents=True, exist_ok=True)
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "voice-pipeline-pod",
                        "-f", str(SSH_KEY)], check=True, stdin=subprocess.DEVNULL)
        if os.name == "nt":
            # Windows ssh refuses a private key that other accounts can read.
            subprocess.run(["icacls", str(SSH_KEY), "/inheritance:r", "/grant:r",
                            f"{getpass.getuser()}:F"], check=False, capture_output=True)
    return SSH_KEY


class Remote:
    """Commands on the pod over SSH, with this script's own key."""

    def __init__(self, host: str, port: int, user: str, key: Path):
        self.target = [f"{user}@{host}", "-p", str(port)]
        self.key = key

    def run(self, script: str, data: bytes | None = None, timeout: float = 120) -> str:
        command = ["ssh", "-i", str(self.key), "-o", "IdentitiesOnly=yes",
                   "-o", "StrictHostKeyChecking=no", "-o", f"UserKnownHostsFile={os.devnull}",
                   "-o", "LogLevel=ERROR", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15",
                   *self.target, script]
        try:
            done = subprocess.run(command, input=data if data is not None else b"",
                                  capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise PodError("The pod did not answer over SSH in time.") from None
        if done.returncode != 0:
            raise PodError(f"A command on the pod failed: "
                           f"{done.stderr.decode(errors='replace').strip()[-500:]}")
        return done.stdout.decode(errors="replace")


def connect(api: RunPod, pod_id: str, key: Path) -> Remote:
    """Wait for the pod's SSH port and for sshd to let this key in."""
    started = time.time()
    while time.time() - started < 300:
        pod = api.pod(pod_id) or {}
        direct = (pod.get("ssh") or {}).get("direct")
        if direct:
            remote = Remote(direct["host"], direct["port"], direct.get("username") or "root", key)
            try:
                remote.run("true", timeout=30)
                return remote
            except PodError:
                pass  # sshd is still starting
        time.sleep(5)
    raise PodError("Could not reach the pod over SSH for 5 minutes.")


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


def upload(remote: Remote, data: bytes, target: str) -> None:
    """Copy bytes to a file on the pod, then check they arrived whole."""
    remote.run(f"cat > {target}", data=data, timeout=600)
    got = remote.run(f"sha256sum {target}").split()[:1]
    if got != [hashlib.sha256(data).hexdigest()]:
        raise PodError("The upload to the pod came out damaged. Run start again.")


def create_pod(api: RunPod, args: argparse.Namespace, token: str,
               public_key: str) -> tuple[dict, dict]:
    need_gb, disk = ENGINES[args.engine]
    options = candidates(api, need_gb, args.max_price, args.community)
    if args.gpu:
        want = args.gpu.lower()
        options = [g for g in options if want in g["name"].lower() or want in g["id"].lower()]
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
                "cloud": gpu["cloud"], "disk": disk, "ports": ["8001/http", "22/tcp"],
                # The image puts PUBLIC_KEY in the pod's authorized_keys at boot.
                "env": {**env, "PUBLIC_KEY": public_key}}
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


def launch_command(env: dict) -> str:
    """The command that starts session.sh on the pod and returns at once.

    An SSH session does not see the pod's own settings, so they are passed
    here. Only session.sh goes to the background: `a && b &` would put the
    whole list there, still attached to the connection, and SSH would wait
    for it (that hung the first real runs on 2026-11-17).
    """
    settings = " ".join(f"{k}={shlex.quote(v)}" for k, v in env.items())
    return (f"cd /root/app || exit 1; {settings} setsid nohup bash voice-pipeline/dev/session.sh "
            "> /root/session.log 2>&1 < /dev/null & echo started")


def follow(remote: Remote) -> str:
    """Print the pod's progress until it is ready or failed. Returns the stage."""
    shown = 0
    misses = 0
    began = quiet_since = time.time()
    while True:
        try:
            out = remote.run("cat /root/stage 2>/dev/null; echo; cat /root/session.log 2>/dev/null",
                             timeout=60)
            misses = 0
        except PodError:
            misses += 1  # a slow answer now and then is fine
            if misses >= 5:
                raise PodError("lost contact with the pod over SSH") from None
            time.sleep(10)
            continue
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

    key = ssh_key()
    public_key = key.with_name(key.name + ".pub").read_text().strip()
    token = secrets.token_urlsafe(12)
    pod, env = create_pod(api, args, token, public_key)
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
        say("Connecting to the pod over SSH...")
        remote = connect(api, pod_id, key)
        say("Uploading the code...")
        remote.run("mkdir -p /root/app")
        upload(remote, make_archive(), "/root/app.tar.gz")
        remote.run("tar -xzf /root/app.tar.gz -C /root/app && rm /root/app.tar.gz")
        say(f"Uploading the voice recording {voice.name}...")
        upload(remote, voice.read_bytes(), "/root/voice.wav")
        say("Starting the setup on the pod. The whole thing takes 10 to 20 minutes;")
        say("progress is shown below. You can close this window at any time: the setup")
        say("carries on, and `status` shows where it is.")
        say()
        remote.run(launch_command(env))
    except KeyboardInterrupt:
        say()
        say(f"Stopped. Pod {pod_id} is still running and billing.")
        say("  python voice-pipeline/dev/pod.py stop     terminates it")
        return
    except PodError:
        say(f"Something went wrong after the pod was created. Pod {pod_id} is still running.")
        if args.yes or ask_yes("Terminate it now so it stops billing?", True):
            terminate(api, pod_id)
        raise
    # From here the setup runs on the pod by itself: losing touch with it
    # here is no reason to remove a pod that is setting up fine.
    try:
        stage = follow(remote)
    except (KeyboardInterrupt, PodError) as e:
        say()
        say(f"Stopped watching{': ' + str(e) if isinstance(e, PodError) else ''}.")
        say(f"The setup carries on on pod {pod_id}, and it keeps billing.")
        say("  python voice-pipeline/dev/pod.py status   shows how it is doing, and the link when ready")
        say("  python voice-pipeline/dev/pod.py stop     terminates it")
        return
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

def server_ready(pod_id: str) -> bool:
    """The call server answers on its public address (anyone can check this)."""
    try:
        with urllib.request.urlopen(f"https://{pod_id}-8001.proxy.runpod.net/health",
                                    timeout=10) as response:
            return response.status == 200
    except Exception:
        return False


def show_pod(api: RunPod, pod: dict) -> None:
    pod = api.pod(pod["id"]) or pod
    env = pod.get("env") or {}
    gpu = (pod.get("gpu") or {}).get("id", "?")
    say(f"Pod {pod['id']} ({pod.get('name')}): {str(pod.get('status')).lower()}, {gpu}, "
        f"${float(pod.get('cost') or 0):.2f}/h, created {pod.get('createdAt', '?')}")
    if pod.get("status") != "RUNNING":
        return
    token = env.get("PIPELINE_DEV_TOKEN")
    if server_ready(pod["id"]):
        say("  setup: ready")
        if token:
            say(f"  make a call: {call_link(pod['id'], token)}")
        return
    lines = api.log_lines(pod["id"])
    if isinstance(lines, str):
        say(f"  setup: in progress (its progress could not be read: {lines})")
    elif lines:
        say("  setup: in progress. Latest:")
        for line in lines[-3:]:
            say("    " + line)
    else:
        say("  setup: in progress (no progress lines yet)")


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
    for pod in pods:
        lines = api.log_lines(pod["id"], tail=2000)
        say(f"===== pod {pod['id']} (last {args.lines} lines)")
        if isinstance(lines, str):
            say(f"(the pod's log could not be read: {lines})")
            continue
        for line in lines[-args.lines:]:
            say(line)


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
    start.add_argument("--engine", choices=sorted(ENGINES), default="ming",
                       help="voice engine (default ming; drama needs an 80 GB GPU)")
    # Until this script can start the pod with the backend, it has no other
    # way to get the voice: the app's recordings live in the backend's storage.
    start.add_argument("--voice", required=True,
                       help="WAV recording of the voice to clone, at least 5 seconds (required)")
    start.add_argument("--llm", help=f"LLM to serve (default {LLM_FOR_CARD})")
    start.add_argument("--max-price", type=float, default=2.5,
                       help="highest price per hour to accept, in USD (default 2.50)")
    start.add_argument("--gpu", help="only rent this GPU type, e.g. L40S (default: the cheapest that fits)")
    start.add_argument("--community", action="store_true",
                       help="also try RunPod's community cloud (cheaper, less reliable)")
    start.add_argument("--idle-minutes", type=int, default=30,
                       help="terminate the pod after this long without a call (default 30, 0 = never)")
    start.add_argument("--another", action="store_true", help="start even if a pod is already running")
    start.add_argument("--yes", action="store_true", help="do not ask before starting or stopping")

    sub.add_parser("status", help="show the team's pod, its progress and its links")
    logs = sub.add_parser("logs", help="show what the pod has been doing")
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
