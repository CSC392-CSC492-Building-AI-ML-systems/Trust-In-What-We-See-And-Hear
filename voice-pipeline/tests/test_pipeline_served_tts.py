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
from pipeline.tts.served import (  # noqa: E402
    RATE, DramaEngine, MingEngine, StreamResampler, audio_events, leveled, to_pcm16)


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
                         client=served(wav(1.0, 44100), seen), stream=False)
    engine.warmed = True
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


# Drama streaming

SOURCE = 44_100


def tone(seconds: float, rate: int = SOURCE, hz: float = 220.0, level: float = 0.3) -> bytes:
    t = np.arange(int(rate * seconds)) / rate
    return (np.sin(2 * np.pi * hz * t) * level * 32767).astype("<i2").tobytes()


def silence(seconds: float, rate: int = SOURCE) -> bytes:
    return bytes(2 * int(rate * seconds))


def sse(*chunks: bytes, done: dict | None = None, chunk_seconds: float = 0.1) -> bytes:
    """vLLM-Omni's event stream for these PCM chunks, split into short deltas."""
    out = []
    step = 2 * int(SOURCE * chunk_seconds)
    for pcm in chunks:
        for i in range(0, len(pcm), step):
            event = {"type": "speech.audio.delta", "response_format": "pcm",
                     "audio": base64.b64encode(pcm[i:i + step]).decode(),
                     "sample_rate": SOURCE, "channels": 1}
            out.append(f"event: speech.audio.delta\ndata: {json.dumps(event)}\n\n")
    final = {"type": "speech.audio.done", "duration": 1.0, **(done or {})}
    out.append(f"event: speech.audio.done\ndata: {json.dumps(final)}\n\n")
    return "".join(out).encode()


def streaming(body: bytes, seen: list) -> DramaEngine:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, content=body,
                              headers={"content-type": "text/event-stream"})
    engine = DramaEngine("http://drama/v1/audio/speech", lambda p: "Hello there.",
                         client=httpx.Client(transport=httpx.MockTransport(handler)))
    engine.warmed = True  # warming up has its own test
    return engine


def seconds_of(pieces) -> float:
    return sum(p.seconds for p in pieces)


def test_drama_asks_for_a_stream(recording):
    seen = []
    engine = streaming(sse(tone(1.0)), seen)
    list(engine.speak("Send the money.", engine.prepare_voice(str(recording))))
    [body] = seen
    assert body["stream"] is True and body["stream_format"] == "sse"
    assert body["response_format"] == "pcm"
    assert body["ref_audio"].startswith("data:audio/wav;base64,")


def test_streamed_audio_comes_out_in_pieces_at_the_protocol_rate(recording):
    engine = streaming(sse(tone(2.0)), [])
    pieces = list(engine.speak("Send the money now please.",
                               engine.prepare_voice(str(recording))))
    assert all(p.sample_rate == RATE for p in pieces)
    assert len(pieces) >= 3  # not one sentence-long piece
    assert pieces[0].seconds < 0.35  # the first piece goes out early
    assert seconds_of(pieces) == pytest.approx(2.0, abs=0.05)


def test_a_sentence_ends_after_a_second_of_silence(recording):
    # The model keeps emitting silence after the words (the recipe warns of it).
    engine = streaming(sse(tone(1.0), silence(5.0)), [])
    pieces = list(engine.speak("Send it.", engine.prepare_voice(str(recording))))
    # The silence is not sent (give or take the 0.1 s chunk where the tone stops).
    assert seconds_of(pieces) == pytest.approx(1.0, abs=0.15)


def test_quiet_speech_is_not_taken_for_silence(recording):
    # On 2026-11-23 a quiet reference gave speech at -35 to -50 dBFS, and a
    # -40 dBFS rule cut real words. Real silence from Drama is -70 or lower.
    quiet = 0.004  # a -48 dBFS sine (-51 dBFS RMS)
    engine = streaming(sse(tone(0.5), tone(2.0, level=quiet)), [])
    pieces = list(engine.speak("Please, I need it now.", engine.prepare_voice(str(recording))))
    assert seconds_of(pieces) == pytest.approx(2.5, abs=0.05)


def test_silence_inside_a_long_chunk_still_ends_the_sentence(recording):
    # Drama's later chunks run over a second: words and silence can share one.
    engine = streaming(sse(tone(1.0), silence(5.0), chunk_seconds=1.5), [])
    pieces = list(engine.speak("Send it.", engine.prepare_voice(str(recording))))
    assert seconds_of(pieces) == pytest.approx(1.0, abs=0.06)


def test_a_quiet_reference_is_brought_to_speaking_level():
    rate = 24000
    t = np.arange(rate * 3) / rate
    speech = (np.sin(2 * np.pi * 200 * t) * 0.02 * (t % 1 < 0.7))  # -37 dBFS, with pauses
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes((speech * 32767).astype("<i2").tobytes())
    out = np.frombuffer(to_pcm16(leveled(buf.getvalue())), dtype="<i2") / 32768
    loud = out[(t % 1 < 0.7)[: len(out)]]
    rms_db = 20 * np.log10(np.sqrt(np.mean(loud ** 2)))
    assert rms_db == pytest.approx(-20, abs=1)
    assert np.abs(out).max() < 10 ** (-1 / 20) + 1e-3  # no clipping


def test_a_loud_reference_is_not_pushed_into_clipping():
    rate = 24000
    t = np.arange(rate * 2) / rate
    speech = np.sin(2 * np.pi * 200 * t) * 0.99
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes((speech * 32767).astype("<i2").tobytes())
    out = np.frombuffer(to_pcm16(leveled(buf.getvalue())), dtype="<i2") / 32768
    assert np.abs(out).max() <= 10 ** (-1 / 20) + 1e-3


def test_a_silent_reference_comes_back_silent(recording):
    out = np.frombuffer(to_pcm16(leveled(recording.read_bytes())), dtype="<i2")
    assert not out.any()


def test_a_pause_inside_a_sentence_is_kept(recording):
    engine = streaming(sse(tone(1.0), silence(0.5), tone(1.0)), [])
    pieces = list(engine.speak("Wait, listen to me.", engine.prepare_voice(str(recording))))
    assert seconds_of(pieces) == pytest.approx(2.5, abs=0.1)


def test_leading_silence_does_not_end_the_sentence(recording):
    engine = streaming(sse(silence(1.5), tone(1.0)), [])
    pieces = list(engine.speak("Hello.", engine.prepare_voice(str(recording))))
    assert seconds_of(pieces) == pytest.approx(2.5, abs=0.1)


def test_a_runaway_sentence_is_cut_at_its_length_cap(recording):
    engine = streaming(sse(tone(30.0)), [])  # sound that never ends
    pieces = list(engine.speak("Send it.", engine.prepare_voice(str(recording))))
    assert engine.max_seconds("Send it.") == pytest.approx(3.2)
    assert seconds_of(pieces) == pytest.approx(3.2, abs=0.2)


def test_an_error_event_reaches_the_turn(recording):
    error = {"type": "speech.audio.error", "error": {"message": "out of memory"}}
    body = f"event: speech.audio.error\ndata: {json.dumps(error)}\n\n".encode()
    engine = streaming(body, [])
    with pytest.raises(RuntimeError, match="out of memory"):
        list(engine.speak("Hello.", engine.prepare_voice(str(recording))))


def test_a_truncated_sentence_is_logged(recording, caplog):
    engine = streaming(sse(tone(1.0), done={"truncated": True}), [])
    list(engine.speak("Hello.", engine.prepare_voice(str(recording))))
    assert "token limit" in caplog.text


def test_events_are_read_one_at_a_time():
    # Each delta is handed on before the next line is read, so audio streams.
    read = []

    def lines():
        for line in sse(tone(0.3), chunk_seconds=0.1).decode().split("\n"):
            read.append(line)
            yield line
    events = audio_events(lines())
    next(events)
    assert len(read) < 5


def test_resampling_does_not_depend_on_how_the_stream_is_cut():
    audio = np.frombuffer(tone(1.0, hz=440.0), dtype="<i2").astype(np.float32) / 32768
    whole = StreamResampler(SOURCE).feed(audio)
    parts, start = [], 0
    resampler = StreamResampler(SOURCE)
    for size in [1, 7, 300, 1234, 5, 4410, 99999]:
        parts.append(resampler.feed(audio[start:start + size]))
        start += size
    assert np.allclose(np.concatenate(parts), whole, atol=1e-6)
    assert len(whole) == pytest.approx(RATE, abs=2)


def test_resampling_keeps_speech_frequencies():
    audio = np.frombuffer(tone(1.0, hz=1000.0), dtype="<i2").astype(np.float32) / 32768
    out = StreamResampler(SOURCE).feed(audio)[200:-200]
    spectrum = np.abs(np.fft.rfft(out))
    peak_hz = np.argmax(spectrum) * RATE / len(out)
    assert peak_hz == pytest.approx(1000.0, abs=5)
    assert np.sqrt(np.mean(out * out)) == pytest.approx(0.3 / np.sqrt(2), rel=0.05)


def test_drama_warms_up_once_when_the_first_voice_is_prepared(recording):
    seen = []
    engine = streaming(sse(tone(0.5)), seen)
    engine.warmed = False
    engine.prepare_voice(str(recording))
    engine.prepare_voice(str(recording))
    assert len(seen) == 1  # one throwaway sentence, for the first call only
