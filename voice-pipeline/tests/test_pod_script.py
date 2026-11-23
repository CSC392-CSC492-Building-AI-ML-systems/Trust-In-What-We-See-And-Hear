"""dev/pod.py against a fake RunPod API and a fake SSH connection.

AI-assisted code (written with Claude Code), cited per the CSC398 AI policy.
"""

import hashlib
import importlib.util
import io
import os
import subprocess
import tarfile
import time
from argparse import Namespace
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("pod", Path(__file__).parents[1] / "dev" / "pod.py")
pod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pod)


def gpu(id, memory, secure=1.0, community=0.5, stock="NONE", maker="NVIDIA"):
    return {"id": id, "name": id.replace("NVIDIA ", ""), "manufacturer": maker, "memory": memory,
            "secure": True, "community": True, "price": {"secure": secure, "community": community},
            "availability": stock}


CATALOG = [
    gpu("NVIDIA L40S", 48, 1.09),
    gpu("NVIDIA RTX A6000", 48, 0.69),
    gpu("NVIDIA RTX 6000 Ada Generation", 48, 1.09, stock="LOW"),
    gpu("NVIDIA GeForce RTX 4090", 24, 0.89, stock="HIGH"),  # too small
    gpu("NVIDIA RTX PRO 6000 Blackwell Server Edition MIG 2g.48gb", 48, 1.39),  # a slice
    gpu("NVIDIA B200", 180, 7.99, stock="LOW"),  # too dear
    gpu("AMD Instinct MI300X OAM", 192, 2.39, maker="AMD"),
    gpu("NVIDIA RTX PRO 6000 Blackwell Server Edition", 96, 2.39, stock="LOW"),
]


class FakeAPI(pod.RunPod):
    """Answers like RunPod; `refuse` lists GPU ids whose create fails for capacity."""

    def __init__(self, refuse=(), pods=()):
        self.refuse = set(refuse)
        self.pods = list(pods)
        self.created = []
        self.files = {}

    def call(self, method, path, body=None, timeout=60):
        if path.startswith("/catalog/gpus"):
            return 200, {"gpus": CATALOG}
        if method == "POST" and path == "/pods":
            self.created.append(body)
            if body["gpu"]["id"] in self.refuse:
                return 400, {"detail": "no instances available"}
            return 201, {"id": "pod123", **body}
        if method == "GET" and path == "/pods":
            return 200, {"pods": self.pods}
        raise AssertionError(f"unexpected {method} {path}")


class LocalRemote(pod.Remote):
    """Runs the pod's commands for real, in a temporary folder standing in for /root."""

    def __init__(self, root):
        self.root = root

    def run(self, script, data=None, timeout=120):
        script = script.replace("/root/", str(self.root) + "/")
        done = subprocess.run(["bash", "-c", script], input=data or b"", capture_output=True)
        if done.returncode:
            raise pod.PodError(done.stderr.decode())
        return done.stdout.decode()


DEV = {"PIPELINE_DEV_TOKEN": "tok"}


def start_args(**kw):
    base = dict(engine="chatterbox", gpu=None, max_price=2.5, community=False, idle_minutes=30,
                llm=None, yes=True, another=False, voice=None, backend=None)
    return Namespace(**{**base, **kw})


def test_gpus_that_fit_are_ranked_in_stock_first_then_by_price():
    found = pod.candidates(FakeAPI(), 48, 2.5, community=False)
    assert [g["id"] for g in found] == [
        "NVIDIA RTX 6000 Ada Generation",                # in stock, cheaper
        "NVIDIA RTX PRO 6000 Blackwell Server Edition",  # in stock
        "NVIDIA RTX A6000",                              # not listed in stock, cheapest
        "NVIDIA L40S",
    ]


def test_create_moves_on_when_a_gpu_is_taken():
    api = FakeAPI(refuse={"NVIDIA RTX 6000 Ada Generation"})
    created, env = pod.create_pod(api, start_args(), DEV, "ssh-ed25519 AAAA test")
    assert created["gpu"]["id"] == "NVIDIA RTX PRO 6000 Blackwell Server Edition"
    first, second = api.created
    assert first["gpu"] == {"id": "NVIDIA RTX 6000 Ada Generation", "count": 1, "minCudaVersion": "12.8"}
    assert first["env"]["LLM_MODEL"] == "Qwen/Qwen3-14B-FP8"
    assert first["env"]["VLLM_UTIL"] == "0.60"
    # A Blackwell card cannot run FP8 here: setup.sh picks the model itself.
    assert "LLM_MODEL" not in second["env"]
    assert second["env"]["PIPELINE_DEV_TOKEN"] == "tok"
    assert second["ports"] == ["8001/http", "22/tcp"] and second["disk"] == 70
    assert second["env"]["PUBLIC_KEY"] == "ssh-ed25519 AAAA test"  # how SSH gets in
    assert "PUBLIC_KEY" not in env  # passed to the setup separately: it is not a setting


def test_create_gives_up_with_advice_when_everything_is_taken():
    api = FakeAPI(refuse={g["id"] for g in CATALOG})
    with pytest.raises(pod.PodError, match="Every GPU that fits is taken"):
        pod.create_pod(api, start_args(), DEV, "key")


def test_drama_asks_for_an_80_gb_card_and_more_disk():
    api = FakeAPI()
    created, _ = pod.create_pod(api, start_args(engine="drama"), DEV, "key")
    assert created["gpu"]["id"] == "NVIDIA RTX PRO 6000 Blackwell Server Edition"
    assert created["disk"] == 150


def test_start_does_not_make_a_second_pod(capsys, monkeypatch, tmp_path):
    api = FakeAPI(pods=[{"id": "old1", "name": "voice-pipeline-test-sam", "status": "RUNNING"}])
    monkeypatch.setattr(api, "pod", lambda pod_id: {**api.pods[0], "status": "STARTING"})
    (tmp_path / "v.wav").write_bytes(b"RIFF....WAVE")
    pod.cmd_start(api, start_args(voice=str(tmp_path / "v.wav")))
    assert api.created == []
    assert "already running" in capsys.readouterr().out


class Stop(Exception):
    pass


def test_a_stopped_pod_is_removed_and_does_not_block_start(capsys, monkeypatch, tmp_path):
    # A pod that could not delete itself stops itself: no GPU, no link, but
    # its disk bills. It must not count as the team's running pod.
    api = FakeAPI(pods=[{"id": "old1", "name": "voice-pipeline-test-sam", "status": "EXITED"}])
    removed = []
    monkeypatch.setattr(pod, "terminate", lambda api, pod_id: removed.append(pod_id))
    monkeypatch.setattr(pod, "ssh_key", lambda: (_ for _ in ()).throw(Stop()))
    (tmp_path / "v.wav").write_bytes(b"RIFF....WAVE")
    with pytest.raises(Stop):  # got as far as making the SSH key for a new pod
        pod.cmd_start(api, start_args(voice=str(tmp_path / "v.wav")))
    assert removed == ["old1"]
    assert "already running" not in capsys.readouterr().out


@pytest.mark.parametrize("failure", [pod.PodError("RunPod answered 502"), KeyboardInterrupt()])
def test_a_failure_right_after_create_still_reports_the_pod(failure, capsys, monkeypatch, tmp_path):
    api = FakeAPI()
    removed = []
    key = tmp_path / "key"
    key.with_name("key.pub").write_text("ssh-ed25519 AAAA test")
    monkeypatch.setattr(pod, "ssh_key", lambda: key)
    monkeypatch.setattr(pod, "terminate", lambda api, pod_id: removed.append(pod_id))

    def earlier_pod(api, mine):
        raise failure
    monkeypatch.setattr(pod, "earlier_pod", earlier_pod)
    (tmp_path / "v.wav").write_bytes(b"RIFF....WAVE")
    try:
        pod.cmd_start(api, start_args(voice=str(tmp_path / "v.wav")))
    except pod.PodError:
        pass
    out = capsys.readouterr().out
    assert "pod123" in out  # the user is told the new pod exists
    if isinstance(failure, pod.PodError):
        assert removed == ["pod123"]  # and, with --yes, it is removed


def test_the_failed_setup_message_is_right_without_an_idle_limit(capsys, monkeypatch, tmp_path):
    api = FakeAPI()
    key = tmp_path / "key"
    key.with_name("key.pub").write_text("ssh-ed25519 AAAA test")
    monkeypatch.setattr(pod, "ssh_key", lambda: key)
    monkeypatch.setattr(pod, "earlier_pod", lambda api, mine: None)
    monkeypatch.setattr(pod, "wait_running", lambda api, pod_id: {"id": pod_id})
    monkeypatch.setattr(pod, "connect", lambda api, pod_id, key: FakeShell())
    monkeypatch.setattr(pod, "make_archive", lambda: b"code")
    monkeypatch.setattr(pod, "follow", lambda remote: "failed")
    # Yes to starting, no to terminating after the failure.
    monkeypatch.setattr(pod, "ask_yes", lambda question, default=False: question.startswith("Start"))
    (tmp_path / "v.wav").write_bytes(b"RIFF....WAVE")
    pod.cmd_start(api, start_args(voice=str(tmp_path / "v.wav"), yes=False, idle_minutes=0))
    out = capsys.readouterr().out
    assert "down in 0 minutes" not in out and "will not shut itself down" in out


def fake_start(monkeypatch, tmp_path, stage="ready"):
    """cmd_start with SSH, the upload and the setup faked. Returns the uploads."""
    key = tmp_path / "key"
    key.with_name("key.pub").write_text("ssh-ed25519 AAAA test")
    shell = FakeShell()
    monkeypatch.setattr(pod, "ssh_key", lambda: key)
    monkeypatch.setattr(pod, "earlier_pod", lambda api, mine: None)
    monkeypatch.setattr(pod, "wait_running", lambda api, pod_id: {"id": pod_id})
    monkeypatch.setattr(pod, "connect", lambda api, pod_id, key: shell)
    monkeypatch.setattr(pod, "make_archive", lambda: b"code")
    monkeypatch.setattr(pod, "follow", lambda remote: stage)
    monkeypatch.setattr(pod, "check_backend", lambda url: None)
    monkeypatch.setattr(pod, "BACKEND_SECRET_FILE", tmp_path / "secret.txt")
    monkeypatch.setattr(pod, "STATE", tmp_path)
    monkeypatch.delenv("PIPELINE_BACKEND_SECRET", raising=False)
    return shell


def test_backend_mode_sends_the_backend_settings_and_no_voice(capsys, monkeypatch, tmp_path):
    api = FakeAPI()
    monkeypatch.setattr(api, "pod", lambda pod_id: {"id": pod_id, "cost": 0.59}, raising=False)
    shell = fake_start(monkeypatch, tmp_path)
    pod.cmd_start(api, start_args(backend="https://api.tunnel.example/"))
    env = api.created[0]["env"]
    secret = (tmp_path / "secret.txt").read_text().strip()
    assert env["PIPELINE_BACKEND_URL"] == "https://api.tunnel.example"
    assert env["PIPELINE_BACKEND_SECRET"] == secret and len(secret) >= 40
    assert "PIPELINE_DEV_TOKEN" not in env
    assert not any("voice.wav" in target for target in shell.files)  # the backend has the voices
    out = capsys.readouterr().out
    assert "PIPELINE_WS_URL=wss://pod123-8001.proxy.runpod.net" in out
    assert secret not in out  # the secret stays in its file, not on screen


def test_backend_mode_reuses_the_saved_secret(monkeypatch, tmp_path):
    fake_start(monkeypatch, tmp_path)
    first = pod.backend_secret()
    assert pod.backend_secret() == first
    monkeypatch.setenv("PIPELINE_BACKEND_SECRET", "from-env")
    assert pod.backend_secret() == "from-env"


@pytest.mark.parametrize("url", ["http://localhost:8000", "localhost:8000", "http://api.example"])
def test_backend_mode_needs_a_public_https_address(url):
    with pytest.raises(pod.PodError, match="public https address"):
        pod.check_backend_url(url)


def test_start_needs_a_voice_or_a_backend():
    with pytest.raises(pod.PodError, match="--voice"):
        pod.cmd_start(FakeAPI(), start_args())


def test_the_archive_has_the_code_and_no_environments():
    names = tarfile.open(fileobj=io.BytesIO(pod.make_archive())).getnames()
    assert "voice-pipeline/dev/session.sh" in names
    assert "voice-pipeline/dev/call.html" in names
    assert "backend/app/modules/calls/context.py" in names
    assert not [n for n in names if "__pycache__" in n or ".venv" in n or "\\" in n]


@pytest.mark.parametrize("name, kept", [
    ("backend/app/main.py", True),
    ("backend/app/.env", False),
    ("backend/app/.env.local", False),
    ("voice-pipeline/me.wav", False),
    ("voice-pipeline/notes/Recording.M4A", False),
])
def test_secrets_and_recordings_are_never_uploaded(name, kept):
    assert pod.never_upload(name) is not kept


def test_untracked_secrets_in_the_folders_are_left_out(monkeypatch, tmp_path):
    for name in ("voice-pipeline/a.py", "voice-pipeline/.env", "voice-pipeline/take.wav",
                 "backend/app/b.py"):
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text("x")
    monkeypatch.setattr(pod, "REPO", tmp_path)  # not a git repository: the walk is used
    assert pod.files_to_upload() == ["backend/app/b.py", "voice-pipeline/a.py"]


class FakeShell(pod.Remote):
    """The two commands upload() runs on the pod, without a real shell."""

    def __init__(self, lossy=False):
        self.files = {}
        self.lossy = lossy

    def run(self, script, data=None, timeout=120):
        if script.startswith("cat > "):
            self.files[script[6:]] = data[:-1] if self.lossy else data
            return ""
        if script.startswith("sha256sum "):
            name = script[10:]
            return f"{hashlib.sha256(self.files[name]).hexdigest()}  {name}\n"
        return ""  # mkdir, tar, the launch


def test_upload_arrives_whole():
    remote = FakeShell()
    data = bytes(range(256)) * 2000
    pod.upload(remote, data, "/root/thing.bin")
    assert remote.files["/root/thing.bin"] == data


def test_a_damaged_upload_is_caught():
    with pytest.raises(pod.PodError, match="damaged"):
        pod.upload(FakeShell(lossy=True), b"0123456789", "/root/thing.bin")


def test_progress_is_shown_once_per_line(capsys):
    # The first read has no stage yet, the next ones do: no line may repeat.
    class Pod:
        reads = [("", ["a"]), ("setup", ["a", "b"]), ("ready", ["a", "b", "c"])]

        def run(self, script, data=None, timeout=120):
            stage, lines = self.reads.pop(0)
            return f"{stage}\n{pod.LOG_START}\n" + "".join(f"{l}\n" for l in lines)
    import unittest.mock
    with unittest.mock.patch.object(pod.time, "sleep", lambda s: None):
        assert pod.follow(Pod()) == "ready"
    assert capsys.readouterr().out.split() == ["a", "b", "c"]


def test_progress_lines_are_read_from_the_container_log(monkeypatch):
    stream = [b'id: 1\n', b'data: {"source":"container","line":"some other output"}\n',
              b'data: {"source":"container","line":"[voice-pipeline] [20:01:02] Step 1 of 3"}\n',
              b'data: not json\n']

    class Stream:
        def __init__(self):
            self.left = list(stream)

        def readline(self):
            return self.left.pop(0) if self.left else b""

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(pod.urllib.request, "urlopen", lambda request, timeout: Stream())
    assert pod.RunPod("key").log_lines("pod123") == ["[20:01:02] Step 1 of 3"]


def test_of_two_starts_at_once_only_the_earlier_pod_stays():
    api = FakeAPI(pods=[{"id": "b2", "name": "voice-pipeline-test-sam", "status": "RUNNING",
                         "createdAt": "2026-11-17T20:00:01Z"},
                        {"id": "a1", "name": "voice-pipeline-test-ali", "status": "RUNNING",
                         "createdAt": "2026-11-17T20:00:00Z"}])
    api.pod = lambda pod_id: next(p for p in api.pods if p["id"] == pod_id)
    assert pod.earlier_pod(api, "b2")["id"] == "a1"  # the later one gives way
    assert pod.earlier_pod(api, "a1") is None          # the earlier one stays


def test_ssh_uses_only_the_scripts_own_key_and_skips_host_prompts(monkeypatch, tmp_path):
    seen = {}

    def fake_run(command, input, capture_output, timeout):
        seen["command"] = command
        return subprocess.CompletedProcess(command, 0, b"hello\n", b"")

    monkeypatch.setattr(pod.subprocess, "run", fake_run)
    remote = pod.Remote("1.2.3.4", 40022, "root", tmp_path / "key")
    assert remote.run("echo hello") == "hello\n"
    command = seen["command"]
    assert command[:3] == ["ssh", "-i", str(tmp_path / "key")]
    assert "IdentitiesOnly=yes" in command and "StrictHostKeyChecking=no" in command
    assert command[-4:] == ["root@1.2.3.4", "-p", "40022", "echo hello"]


@pytest.mark.skipif(os.name == "nt", reason="needs a POSIX shell with setsid, like the pod's")
def test_the_launch_returns_at_once_while_the_setup_keeps_running(tmp_path, monkeypatch):
    # Captured output works like SSH: the call ends only when nothing holds
    # the connection's output open. A setup that keeps running must not.
    (tmp_path / "app" / "voice-pipeline" / "dev").mkdir(parents=True)
    (tmp_path / "app" / "voice-pipeline" / "dev" / "session.sh").write_text(
        'echo "$PIPELINE_DEV_TOKEN" > ../token; sleep 30\n')
    shim = tmp_path / "bin"  # setsid is Linux-only; on macOS a stand-in runs the command
    shim.mkdir()
    (shim / "setsid").write_text('#!/bin/sh\nexec "$@"\n')
    (shim / "setsid").chmod(0o755)
    monkeypatch.setenv("PATH", f"{shim}:{pod.os.environ['PATH']}")
    remote = LocalRemote(tmp_path)
    started = time.time()
    assert remote.run(pod.launch_command({"PIPELINE_DEV_TOKEN": "a b'c"})).strip() == "started"
    assert time.time() - started < 5
    for _ in range(50):  # the setup got its settings, quotes and all
        if (tmp_path / "token").is_file() and (tmp_path / "token").read_text().strip():
            break
        time.sleep(0.1)
    assert (tmp_path / "token").read_text().strip() == "a b'c"
