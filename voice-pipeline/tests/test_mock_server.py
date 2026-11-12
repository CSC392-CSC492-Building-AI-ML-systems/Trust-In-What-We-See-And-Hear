"""Tests for the mock call server: it must follow PROTOCOL.md.

AI-assisted code (written with Claude Code), cited per the CSC398 AI policy.
"""

import array
import base64
import io
import math
import sys
import time
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

sys.path.insert(0, str(Path(__file__).parents[1]))
import mock_server  # noqa: E402

URL = "/ws/calls/call-1?token=dev"


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(mock_server, "CHUNK_DELAY", 0)
    monkeypatch.setattr(mock_server, "MAX_SECONDS", 60)
    monkeypatch.setattr(mock_server, "END_AFTER_TURNS", 0)


@pytest.fixture
def client():
    return TestClient(mock_server.app)


def pcm(seconds: float, amplitude: int) -> bytes:
    n = int(seconds * mock_server.IN_RATE)
    samples = array.array(
        "h", (int(amplitude * math.sin(2 * math.pi * 200 * i / mock_server.IN_RATE)) for i in range(n))
    )
    if sys.byteorder == "big":
        samples.byteswap()
    return samples.tobytes()


def send_audio(ws, data: bytes, frame_bytes: int = 1280) -> None:
    for i in range(0, len(data), frame_bytes):
        ws.send_bytes(data[i : i + frame_bytes])


def read_turn(ws, turn_id: int) -> list[dict]:
    """Receive messages up to and including this turn's turn_done."""
    messages = []
    while True:
        msg = ws.receive_json()
        messages.append(msg)
        if msg["type"] == "turn_done":
            assert msg["turn_id"] == turn_id
            return messages


def answer(ws) -> list[dict]:
    assert ws.receive_json()["type"] == "ready"
    ws.send_json({"type": "answered"})
    return read_turn(ws, 1)


def test_missing_token_is_refused(client):
    with client.websocket_connect("/ws/calls/call-1") as ws:
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
    assert closed.value.code == 4401


def test_ready_describes_the_call(client):
    with client.websocket_connect(URL) as ws:
        assert ws.receive_json() == {
            "type": "ready", "call_id": "call-1", "input_sample_rate": 16000,
            "output_sample_rate": 24000, "max_duration_seconds": 60,
        }


def test_caller_speaks_first_after_answered(client):
    with client.websocket_connect(URL) as ws:
        messages = answer(ws)
    audio = messages[:-1]
    assert [m["type"] for m in audio] == ["audio"] * len(mock_server.OPENING)
    assert [m["seq"] for m in audio] == list(range(len(audio)))
    assert [m["text"] for m in audio] == mock_server.OPENING
    for m in audio:
        assert m["turn_id"] == 1
        with wave.open(io.BytesIO(base64.b64decode(m["audio"]))) as w:
            assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 24000)
            assert w.getnframes() > 0


def test_speech_then_silence_gets_a_reply(client):
    with client.websocket_connect(URL) as ws:
        answer(ws)
        ws.send_json({"type": "played", "turn_id": 1})
        # An odd frame size checks that samples split across frames still work.
        send_audio(ws, pcm(1.0, 8000) + pcm(0.7, 0), frame_bytes=1001)
        end = ws.receive_json()
        assert end == {"type": "speech_end", "seconds": 1.0}
        messages = read_turn(ws, 2)
    assert messages[0]["type"] == "transcript" and messages[0]["turn_id"] == 2
    assert [m["type"] for m in messages[1:-1]] == ["audio"] * len(mock_server.REPLIES[0])


def test_short_noise_is_not_a_turn(client):
    with client.websocket_connect(URL) as ws:
        answer(ws)
        ws.send_json({"type": "played", "turn_id": 1})
        send_audio(ws, pcm(0.1, 8000) + pcm(0.7, 0))
        ws.send_json({"type": "ping"})
        assert ws.receive_json() == {"type": "pong"}


def test_audio_is_ignored_until_played(client):
    with client.websocket_connect(URL) as ws:
        answer(ws)
        send_audio(ws, pcm(1.0, 8000) + pcm(0.7, 0))
        ws.send_json({"type": "ping"})
        assert ws.receive_json() == {"type": "pong"}


def test_audio_before_answered_is_ignored(client):
    with client.websocket_connect(URL) as ws:
        assert ws.receive_json()["type"] == "ready"
        send_audio(ws, pcm(1.0, 8000) + pcm(0.7, 0))
        ws.send_json({"type": "ping"})
        assert ws.receive_json() == {"type": "pong"}


def test_listening_resumes_without_played_once_audio_has_run_out(client, monkeypatch):
    with client.websocket_connect(URL) as ws:
        answer(ws)
        # Move only the mock's clock forward, not the event loop's.
        later = SimpleNamespace(monotonic=lambda: time.monotonic() + 3600)
        monkeypatch.setattr(mock_server, "time", later)
        send_audio(ws, pcm(1.0, 8000) + pcm(0.7, 0))
        assert ws.receive_json()["type"] == "speech_end"


def test_hangup_ends_the_call(client):
    with client.websocket_connect(URL) as ws:
        answer(ws)
        ws.send_json({"type": "hangup"})
        assert ws.receive_json() == {"type": "call_ended", "reason": "hangup"}
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
    assert closed.value.code == 1000


def test_time_limit_ends_the_call(client, monkeypatch):
    monkeypatch.setattr(mock_server, "MAX_SECONDS", 0.05)
    with client.websocket_connect(URL) as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "answered"})
        while (msg := ws.receive_json())["type"] != "call_ended":
            pass
        assert msg["reason"] == "time_limit"


def test_time_limit_starts_at_answered(client, monkeypatch):
    monkeypatch.setattr(mock_server, "MAX_SECONDS", 0.05)
    with client.websocket_connect(URL) as ws:
        assert ws.receive_json()["type"] == "ready"
        time.sleep(0.15)
        ws.send_json({"type": "ping"})
        assert ws.receive_json() == {"type": "pong"}


def test_completed_after_configured_turns(client, monkeypatch):
    monkeypatch.setattr(mock_server, "END_AFTER_TURNS", 1)
    with client.websocket_connect(URL) as ws:
        answer(ws)
        assert ws.receive_json() == {"type": "call_ended", "reason": "completed"}


@pytest.mark.parametrize("send", [
    lambda ws: ws.send_text("not json"),
    lambda ws: ws.send_json([1, 2]),
    lambda ws: ws.send_json({"no": "type"}),
    lambda ws: ws.send_json({"type": "nonsense"}),
])
def test_bad_messages_report_an_error_and_the_call_continues(client, send):
    with client.websocket_connect(URL) as ws:
        assert ws.receive_json()["type"] == "ready"
        send(ws)
        assert ws.receive_json()["type"] == "error"
        ws.send_json({"type": "ping"})
        assert ws.receive_json() == {"type": "pong"}
