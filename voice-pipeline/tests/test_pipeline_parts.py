"""Tests for the pieces around the call: LLM client, call setup, GPU sharing.

AI-assisted code (written with Claude Code), cited per the CSC398 AI policy.
"""

import asyncio
import json
import sys
import threading
import wave
from contextlib import aclosing
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
from pipeline import calls as calls_module  # noqa: E402
from pipeline.calls import (  # noqa: E402
    BackendCalls, CallsUnavailable, DevCalls, VoiceError, fetch_voice, wav_seconds)
from pipeline.context import CallContext  # noqa: E402
from pipeline.llm import ChatLLM  # noqa: E402
from pipeline.models import Models  # noqa: E402
from pipeline.parts import EngineInfo, Piece  # noqa: E402


def served_by(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# ---------------------------------------------------------------- LLM client
def sse(*deltas: str) -> str:
    events = [json.dumps({"choices": [{"delta": {"content": d}}]}) for d in deltas]
    events.insert(0, json.dumps({"choices": [{"delta": {"role": "assistant"}}]}))
    return "".join(f"data: {e}\n\n" for e in events) + "data: [DONE]\n\n"


def test_llm_streams_text_and_sends_the_call_settings():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(200, text=sse("Hey, ", "it's me."))

    async def run():
        llm = ChatLLM("http://llm/v1/chat/completions", "some-model", client=served_by(handler))
        return [d async for d in llm.stream([{"role": "user", "content": "hi"}])]

    assert asyncio.run(run()) == ["Hey, ", "it's me."]
    body = requests[0]
    assert body["model"] == "some-model" and body["stream"] is True
    assert body["messages"] == [{"role": "user", "content": "hi"}]
    assert body["chat_template_kwargs"] == {"enable_thinking": False}


def test_llm_error_status_raises():
    async def run():
        llm = ChatLLM("http://llm/x", "m", client=served_by(lambda r: httpx.Response(503)))
        return [d async for d in llm.stream([])]

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(run())


# ---------------------------------------------------------------- call setup
def test_dev_calls_hand_out_the_sample_context_for_the_right_token(tmp_path):
    calls = DevCalls("secret", "voice.wav", max_duration_seconds=120)
    assert asyncio.run(calls.claim("call-1", "nope")) is None
    setup = asyncio.run(calls.claim("call-1", "secret"))
    assert (setup.voice, setup.max_duration_seconds) == ("voice.wav", 120)
    ctx, _ = CallContext.from_dict(setup.context)  # the backend's sample is valid
    assert ctx.persona.name == "Sam"


def test_backend_calls_claim_with_the_secret_and_the_token():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, str(request.url), request.headers["authorization"],
                     json.loads(request.content)))
        return httpx.Response(200, json={
            "context": {"persona": {"name": "Sam"}},
            "voice_url": "https://storage.example/v.wav?sig=1", "max_duration_seconds": 180})

    calls = BackendCalls("https://api.example/", "s3cret", served_by(handler))
    setup = asyncio.run(calls.claim("id/../x", "tok"))
    assert seen == [("POST", "https://api.example/api/v1/internal/calls/id%2F..%2Fx/claim",
                     "Bearer s3cret", {"token": "tok"})]
    assert setup.context == {"persona": {"name": "Sam"}}
    assert (setup.voice, setup.max_duration_seconds) == ("https://storage.example/v.wav?sig=1", 180)


@pytest.mark.parametrize("status", [403, 404])
def test_backend_calls_refuse_a_token_the_backend_refuses(status):
    calls = BackendCalls("https://api.example", "s", served_by(lambda r: httpx.Response(status)))
    assert asyncio.run(calls.claim("call-1", "tok")) is None


def test_backend_calls_report_a_backend_that_cannot_answer():
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    for handler in (down, lambda r: httpx.Response(401), lambda r: httpx.Response(500),
                    lambda r: httpx.Response(200, json={"context": {}}),
                    lambda r: httpx.Response(200, text="not json")):
        calls = BackendCalls("https://api.example", "s", served_by(handler))
        with pytest.raises(CallsUnavailable):
            asyncio.run(calls.claim("call-1", "tok"))


def test_voice_is_downloaded_to_a_temporary_file():
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://storage.example/voice.wav?sig=abc"
        return httpx.Response(200, content=b"RIFFdata")

    path, temporary = asyncio.run(
        fetch_voice("https://storage.example/voice.wav?sig=abc", served_by(handler)))
    try:
        assert temporary and path.read_bytes() == b"RIFFdata"
    finally:
        path.unlink()


def test_voice_download_problems_leave_no_file_behind(monkeypatch, tmp_path):
    monkeypatch.setattr(calls_module.tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(calls_module, "MAX_VOICE_BYTES", 4)
    for handler in (lambda r: httpx.Response(404),
                    lambda r: httpx.Response(200, content=b"too large")):
        with pytest.raises(VoiceError):
            asyncio.run(fetch_voice("https://storage.example/voice.wav", served_by(handler)))
    with pytest.raises(VoiceError):  # not encrypted
        asyncio.run(fetch_voice("http://storage.example/voice.wav"))
    with pytest.raises(VoiceError):
        asyncio.run(fetch_voice(str(tmp_path / "missing.wav")))
    assert list(tmp_path.iterdir()) == []


def test_wav_seconds(tmp_path):
    path = tmp_path / "v.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(bytes(2 * 36000))
    assert wav_seconds(path) == 1.5
    path.write_bytes(b"not audio")
    with pytest.raises(VoiceError):
        wav_seconds(path)


# ---------------------------------------------------------------- GPU sharing
class SlowTTS:
    """Yields one piece, then waits to be released before the second."""

    info = EngineInfo("slow", 24000, 1.0)

    def __init__(self):
        self.release = threading.Event()
        self.finished = False

    def speak(self, text, voice, first_in_turn):
        yield Piece(b"\0\0", 24000)
        self.release.wait(5)
        self.finished = True
        yield Piece(b"\0\0", 24000)


def test_a_cancelled_turn_keeps_the_gpu_until_the_engine_has_stopped():
    """Otherwise another call could start speaking while this voice is loaded."""
    tts = SlowTTS()

    async def run():
        models = Models(stt=None, tts=tts, llm=None, new_segmenter=None)
        got_first = asyncio.Event()

        async def turn():
            async with aclosing(models.speak("hello", "voice", True)) as pieces:
                async for _ in pieces:
                    got_first.set()

        task = asyncio.create_task(turn())
        await got_first.wait()
        task.cancel()
        await asyncio.sleep(0.05)
        held_while_engine_runs = models.gpu.locked() and not tts.finished
        tts.release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        return held_while_engine_runs, models.gpu.locked(), tts.finished

    assert asyncio.run(run()) == (True, False, True)


def test_an_engine_error_reaches_the_turn_and_frees_the_gpu():
    class BrokenTTS:
        info = EngineInfo("broken", 24000, 1.0)

        def speak(self, text, voice, first_in_turn):
            raise ValueError("no such voice")
            yield

    async def run():
        models = Models(stt=None, tts=BrokenTTS(), llm=None, new_segmenter=None)
        with pytest.raises(ValueError):
            async with aclosing(models.speak("hello", "voice", True)) as pieces:
                async for _ in pieces:
                    pass
        return models.gpu.locked()

    assert asyncio.run(run()) is False
