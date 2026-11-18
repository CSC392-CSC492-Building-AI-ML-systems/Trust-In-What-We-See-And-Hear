"""dev/pod.py against a fake RunPod API and a fake SSH connection.

AI-assisted code (written with Claude Code), cited per the CSC398 AI policy.
"""

import importlib.util
import io
import subprocess
import tarfile
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


def start_args(**kw):
    base = dict(engine="chatterbox", max_price=2.5, community=False, idle_minutes=30,
                llm=None, yes=True, another=False, voice=None)
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
    created, env = pod.create_pod(api, start_args(), "tok", "ssh-ed25519 AAAA test")
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
        pod.create_pod(api, start_args(), "tok", "key")


def test_drama_asks_for_an_80_gb_card_and_more_disk():
    api = FakeAPI()
    created, _ = pod.create_pod(api, start_args(engine="drama"), "tok", "key")
    assert created["gpu"]["id"] == "NVIDIA RTX PRO 6000 Blackwell Server Edition"
    assert created["disk"] == 150


def test_start_does_not_make_a_second_pod(capsys, monkeypatch, tmp_path):
    api = FakeAPI(pods=[{"id": "old1", "name": "voice-pipeline-test-sam", "status": "EXITED"}])
    monkeypatch.setattr(api, "pod", lambda pod_id: api.pods[0])
    (tmp_path / "v.wav").write_bytes(b"RIFF....WAVE")
    pod.cmd_start(api, start_args(voice=str(tmp_path / "v.wav")))
    assert api.created == []
    assert "already running" in capsys.readouterr().out


def test_the_archive_has_the_code_and_no_environments():
    names = tarfile.open(fileobj=io.BytesIO(pod.make_archive())).getnames()
    assert "voice-pipeline/dev/session.sh" in names
    assert "voice-pipeline/dev/call.html" in names
    assert "backend/app/modules/calls/context.py" in names
    assert not [n for n in names if "__pycache__" in n or ".venv" in n or "\\" in n]


def test_upload_arrives_whole(tmp_path):
    data = bytes(range(256)) * 2000
    pod.upload(LocalRemote(tmp_path), data, "/root/thing.bin")
    assert (tmp_path / "thing.bin").read_bytes() == data


def test_a_damaged_upload_is_caught(tmp_path):
    class Lossy(LocalRemote):
        def run(self, script, data=None, timeout=120):
            return super().run(script, data[:-1] if data else data, timeout)
    with pytest.raises(pod.PodError, match="damaged"):
        pod.upload(Lossy(tmp_path), b"0123456789", "/root/thing.bin")


def test_progress_lines_are_read_from_the_container_log(monkeypatch):
    stream = [b'id: 1\n', b'data: {"source":"container","line":"some other output"}\n',
              b'data: {"source":"container","line":"[voice-pipeline] [20:01:02] Step 1 of 3"}\n',
              b'data: not json\n']

    class Stream:
        def __enter__(self):
            return iter(stream)

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
