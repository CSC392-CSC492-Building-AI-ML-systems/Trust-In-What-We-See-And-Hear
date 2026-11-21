"""Tests for the voice engines that run as their own server (tts/served.py),
with a fake server. Need numpy and soundfile, so they are skipped in the
backend's environment.

AI-assisted code (written with Claude Code), cited per the CSC398 AI policy.
"""

import base64
import io
import json
import sys
import wave
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("soundfile")
httpx = pytest.importorskip("httpx")

sys.path.insert(0, str(Path(__file__).parents[1]))
from pipeline.tts.served import RATE, DramaEngine, MingEngine, to_pcm16  # noqa: E402


def wav(seconds: float, rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(2 * int(rate * seconds)))
    return buf.getvalue()


def served(answer: bytes, seen: list) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((str(request.url), json.loads(request.content)))
        return httpx.Response(200, content=answer)
    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture
def recording(tmp_path) -> Path:
    path = tmp_path / "voice.wav"
    path.write_bytes(wav(4.0, 24000))
    return path


def test_any_rate_comes_back_at_the_protocol_rate():
    assert len(to_pcm16(wav(1.0, 44100))) == pytest.approx(2 * RATE, abs=4)
    assert len(to_pcm16(wav(0.5, RATE))) == RATE


def test_preparing_a_voice_transcribes_the_recording(recording):
    engine = MingEngine("http://ming/speak", transcribe=lambda path: " Hello there. ")
    voice = engine.prepare_voice(str(recording))
    assert (voice.text, voice.wav) == ("Hello there.", recording.read_bytes())


def test_a_recording_with_no_words_is_refused(recording):
    engine = MingEngine("http://ming/speak", transcribe=lambda path: "  ")
    with pytest.raises(ValueError):
        engine.prepare_voice(str(recording))


def test_ming_sends_the_recording_and_its_words(recording):
    seen = []
    engine = MingEngine("http://ming/speak", lambda p: "Hello there.",
                        client=served(wav(1.0, 44100), seen))
    pieces = list(engine.speak("Send the money.", engine.prepare_voice(str(recording))))
    [(url, body)] = seen
    assert url == "http://ming/speak"
    assert body["text"] == "Send the money." and body["ref_text"] == "Hello there."
    assert base64.b64decode(body["ref_audio"]) == recording.read_bytes()
    assert [p.sample_rate for p in pieces] == [RATE]


def test_drama_sends_the_recording_as_a_data_url(recording):
    seen = []
    engine = DramaEngine("http://drama/v1/audio/speech", lambda p: "Hello there.",
                         client=served(wav(1.0, 44100), seen))
    list(engine.speak("Send the money.", engine.prepare_voice(str(recording))))
    [(_, body)] = seen
    assert body["model"] == "fishaudio/drama-3" and body["input"] == "Send the money."
    assert body["ref_audio"].startswith("data:audio/wav;base64,")
    assert body["ref_text"] == "Hello there." and body["response_format"] == "wav"


def test_a_server_error_reaches_the_turn(recording):
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    engine = MingEngine("http://ming/speak", lambda p: "Hi there.", client=client)
    with pytest.raises(httpx.HTTPStatusError):
        list(engine.speak("Hello.", engine.prepare_voice(str(recording))))
