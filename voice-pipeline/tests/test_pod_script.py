"""dev/pod.py against a fake RunPod API: choosing a GPU, uploading, starting.

AI-assisted code (written with Claude Code), cited per the CSC398 AI policy.
"""

import base64
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
        if method == "POST" and path.endswith("/exec"):
            return 200, self.exec(body["command"])
        raise AssertionError(f"unexpected {method} {path}")

    def exec(self, command):
        """Runs the upload's shell commands for real, in a temporary folder."""
        script = command[2].replace("/root/", str(self.root) + "/")
        done = subprocess.run(["bash", "-c", script, *command[3:]], capture_output=True, text=True)
        return {"stdout": done.stdout, "stderr": done.stderr, "exitCode": done.returncode}


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
    created, env = pod.create_pod(api, start_args(), "tok")
    assert created["gpu"]["id"] == "NVIDIA RTX PRO 6000 Blackwell Server Edition"
    first, second = api.created
    assert first["gpu"] == {"id": "NVIDIA RTX 6000 Ada Generation", "count": 1, "minCudaVersion": "12.8"}
    assert first["env"]["LLM_MODEL"] == "Qwen/Qwen3-14B-FP8"
    assert first["env"]["VLLM_UTIL"] == "0.60"
    # A Blackwell card cannot run FP8 here: setup.sh picks the model itself.
    assert "LLM_MODEL" not in second["env"]
    assert second["env"]["PIPELINE_DEV_TOKEN"] == "tok"
    assert second["ports"] == ["8001/http"] and second["disk"] == 70


def test_create_gives_up_with_advice_when_everything_is_taken():
    api = FakeAPI(refuse={g["id"] for g in CATALOG})
    with pytest.raises(pod.PodError, match="Every GPU that fits is taken"):
        pod.create_pod(api, start_args(), "tok")


def test_drama_asks_for_an_80_gb_card_and_more_disk():
    api = FakeAPI()
    created, _ = pod.create_pod(api, start_args(engine="drama"), "tok")
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


def test_upload_arrives_whole_in_several_pieces(tmp_path):
    api = FakeAPI()
    api.root = tmp_path
    data = bytes(range(256)) * 2000  # 512 KB: several requests of pieces
    pod.upload(api, "pod123", data, "/root/thing.bin")
    assert (tmp_path / "thing.bin").read_bytes() == data
    assert not (tmp_path / "thing.bin.b64").exists()
    assert len(base64.b64encode(data)) > 4000 * 60  # it did take more than one request


def test_of_two_starts_at_once_only_the_earlier_pod_stays():
    api = FakeAPI(pods=[{"id": "b2", "name": "voice-pipeline-test-sam", "status": "RUNNING",
                         "createdAt": "2026-11-17T20:00:01Z"},
                        {"id": "a1", "name": "voice-pipeline-test-ali", "status": "RUNNING",
                         "createdAt": "2026-11-17T20:00:00Z"}])
    api.pod = lambda pod_id: next(p for p in api.pods if p["id"] == pod_id)
    assert pod.earlier_pod(api, "b2")["id"] == "a1"  # the later one gives way
    assert pod.earlier_pod(api, "a1") is None          # the earlier one stays
