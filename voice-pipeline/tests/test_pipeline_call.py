"""Tests for the real call session, with fake models behind the same seams.

The session, the model glue (threads and the GPU lock) and the WebSocket are
real. Only the four models are fakes, so this runs without a GPU.

AI-assisted code (written with Claude Code), cited per the CSC398 AI policy.
"""

import asyncio
import base64
import copy
import io
import json
import logging
import re
import sys
import time
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

sys.path.insert(0, str(Path(__file__).parents[1]))
from pipeline import session as session_module  # noqa: E402
from pipeline.calls import CallsUnavailable, DevCalls  # noqa: E402
from pipeline.config import Settings  # noqa: E402
from pipeline.models import Models  # noqa: E402
from pipeline.parts import EngineInfo, Heard, Piece, Utterance  # noqa: E402
from pipeline.session import create_app  # noqa: E402

URL = "/ws/calls/call-1?token=secret"
SPEECH = b"\x01\x10" * 640  # one 40 ms microphone frame that is not silence
SILENCE = bytes(1280)


class FakeSegmenter:
    """An utterance ends when a silent frame follows frames that are not."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.heard = b""

    def feed(self, data):
        if any(data):
            self.heard += data
            return []
        if not self.heard:
            return []
        utterance = Utterance(self.heard, len(self.heard) / 2 / 16000)
        self.heard = b""
        return [utterance]


class FakeSTT:
    def __init__(self):
        self.results = []
        self.hints = []

    def transcribe(self, audio, hint_words):
        self.hints.append(hint_words)
        return self.results.pop(0) if self.results else Heard("Who is this?", True)


GRADE = {"outcome": "recognized_scam", "score": 90, "summary": "You refused to pay.",
         "did_well": ["You asked who was calling."], "to_improve": [],
         "red_flags_noticed": ["urgency"], "red_flags_missed": [],
         "sensitive_info_shared": False}


class FakeLLM:
    model = "fake-llm"

    def __init__(self, *replies):
        self.replies = list(replies)
        self.seen = []  # the messages of each request
        self.grades = []  # what complete() answers; GRADE once these run out
        self.graded = []  # the messages of each scoring request

    async def complete(self, messages):
        self.graded.append(copy.deepcopy(messages))
        grade = self.grades.pop(0) if self.grades else json.dumps(GRADE)
        if isinstance(grade, Exception):
            raise grade
        return grade

    async def stream(self, messages):
        self.seen.append(copy.deepcopy(messages))
        reply = self.replies.pop(0) if self.replies else "I really need your help with this today."
        if isinstance(reply, Exception):
            raise reply
        for word in re.split(r"(?<= )", reply):
            yield word


class FakeTTS:
    def __init__(self, sound_tags=()):
        self.info = EngineInfo("fake", 24000, min_reference_seconds=1.0, sound_tags=sound_tags)
        self.said = []  # (text, voice)

    def prepare_voice(self, wav_path):
        return f"voice of {Path(wav_path).name}"

    def speak(self, text, voice, first_in_turn):
        self.said.append((text, voice))
        for _ in range(2):
            yield Piece(bytes(2 * 2400), 24000)  # 0.1 s


def write_wav(path: Path, seconds: float) -> Path:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(bytes(2 * int(24000 * seconds)))
    return path


@pytest.fixture
def rig(tmp_path):
    """The pieces of one server; tests adjust them before connecting."""
    r = SimpleNamespace(
        stt=FakeSTT(), llm=FakeLLM(), tts=FakeTTS(),
        calls=DevCalls("secret", str(write_wav(tmp_path / "friend.wav", 6.0))),
        settings=Settings(played_grace_seconds=0.05))
    r.client = lambda: TestClient(create_app(
        Models(r.stt, r.tts, r.llm, FakeSegmenter), r.calls, r.settings))
    return r


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
    turn = read_turn(ws, 1)
    ws.send_json({"type": "played", "turn_id": 1})
    return turn


def say_something(ws) -> None:
    for _ in range(10):
        ws.send_bytes(SPEECH)
    ws.send_bytes(SILENCE)


def spoken_text(messages: list[dict]) -> str:
    return " ".join(m["text"] for m in messages if m["type"] == "audio" and m["text"])


# ---------------------------------------------------------------- connecting
@pytest.mark.parametrize("url", ["/ws/calls/call-1", "/ws/calls/call-1?token=wrong"])
def test_missing_or_wrong_token_closes_with_4401(rig, url):
    with rig.client().websocket_connect(url) as ws:
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
    assert closed.value.code == 4401


def test_no_token_configured_refuses_everyone(rig):
    rig.calls.token = ""
    with rig.client().websocket_connect("/ws/calls/call-1?token=") as ws:
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
    assert closed.value.code == 4401


def test_ready_reports_rates_and_the_time_limit(rig):
    rig.calls.max_duration_seconds = 9999
    with rig.client().websocket_connect(URL) as ws:
        assert ws.receive_json() == {
            "type": "ready", "call_id": "call-1", "input_sample_rate": 16000,
            "output_sample_rate": 24000, "max_duration_seconds": 600.0}
        ws.send_json({"type": "hangup"})
        assert ws.receive_json() == {"type": "call_ended", "reason": "hangup"}


@pytest.mark.parametrize("problem", ["context", "name", "short voice", "no voice"])
def test_a_call_that_cannot_be_set_up_ends_with_an_error(rig, tmp_path, problem):
    if problem in ("context", "name"):
        bad = tmp_path / "context.json"
        context = {"persona": {"name": "Sam"}}  # no scenario
        if problem == "name":  # valid for the contract, but nothing left to say
            context = {"persona": {"name": "[*]"},
                       "scenario": {"title": "t", "situation": "s", "ask": "a"}}
        bad.write_text(json.dumps(context))
        rig.calls.context_path = bad
    elif problem == "short voice":
        rig.calls.voice = str(write_wav(tmp_path / "short.wav", 0.5))
    else:
        rig.calls.voice = str(tmp_path / "missing.wav")
    with rig.client().websocket_connect(URL) as ws:
        assert ws.receive_json()["code"] == "setup_failed"
        assert ws.receive_json() == {"type": "call_ended", "reason": "error"}
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
    assert closed.value.code == 1000


def test_calls_beyond_the_limit_are_told_the_lines_are_busy(rig):
    rig.settings = Settings(max_calls=1)
    client = rig.client()
    with client.websocket_connect(URL) as first:
        assert first.receive_json()["type"] == "ready"
        with client.websocket_connect(URL) as second:
            assert second.receive_json()["code"] == "busy"
            assert second.receive_json() == {"type": "call_ended", "reason": "error"}
        first.send_json({"type": "hangup"})
        assert first.receive_json()["type"] == "call_ended"
    with client.websocket_connect(URL) as third:  # the slot was given back
        assert third.receive_json()["type"] == "ready"


def test_a_busy_server_does_not_spend_the_token(rig):
    claimed = []
    claim = rig.calls.claim

    async def counting(call_id, token):
        claimed.append(call_id)
        return await claim(call_id, token)

    rig.calls.claim = counting
    rig.settings = Settings(max_calls=1)
    client = rig.client()
    with client.websocket_connect(URL) as first:
        assert first.receive_json()["type"] == "ready"
        with client.websocket_connect("/ws/calls/call-2?token=secret") as second:
            assert second.receive_json()["code"] == "busy"
        first.send_json({"type": "hangup"})
    assert claimed == ["call-1"]


def test_a_backend_that_cannot_be_asked_ends_the_call_with_an_error(rig):
    async def down(call_id, token):
        raise CallsUnavailable("the backend could not be reached")

    rig.calls.claim = down
    client = rig.client()
    with client.websocket_connect(URL) as ws:
        assert ws.receive_json()["code"] == "setup_failed"
        assert ws.receive_json() == {"type": "call_ended", "reason": "error"}
    rig.calls = DevCalls("secret", rig.calls.voice)
    with rig.client().websocket_connect(URL) as ws:
        assert ws.receive_json()["type"] == "ready"


# ---------------------------------------------------------------- turns
def test_the_caller_speaks_first_in_the_personas_voice(rig):
    rig.llm.replies = ["Alex? It's me, it's Sam. I'm in really big trouble and I need your help."]
    with rig.client().websocket_connect(URL) as ws:
        turn = answer(ws)
    audio = [m for m in turn if m["type"] == "audio"]
    assert [m["seq"] for m in audio] == [0, 1, 2, 3]
    # Each chunk's words come with its first piece of audio.
    assert [m["text"] for m in audio] == [
        "Alex? It's me, it's Sam.", "",
        "I'm in really big trouble and I need your help.", ""]
    with wave.open(io.BytesIO(base64.b64decode(audio[0]["audio"]))) as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (24000, 1, 2)
        assert w.getnframes() == 2400
    assert {voice for _, voice in rig.tts.said} == {"voice of friend.wav"}
    system, opening = rig.llm.seen[0]
    assert system["role"] == "system" and "You are Sam" in system["content"]
    assert opening["role"] == "user" and "just connected" in opening["content"]


def test_a_hands_free_turn(rig):
    with rig.client().websocket_connect(URL) as ws:
        answer(ws)
        say_something(ws)
        assert ws.receive_json() == {"type": "speech_end", "seconds": 0.4}
        turn = read_turn(ws, 2)
    assert turn[0] == {"type": "transcript", "turn_id": 2, "text": "Who is this?"}
    assert spoken_text(turn) == "I really need your help with this today."
    assert rig.stt.hints == ["Sam Alex"]
    roles = [m["role"] for m in rig.llm.seen[1]]
    assert roles == ["system", "user", "assistant", "user"]
    assert rig.llm.seen[1][-1]["content"] == "Who is this?"


def test_microphone_is_ignored_until_the_turn_was_played(rig):
    with rig.client().websocket_connect(URL) as ws:
        assert ws.receive_json()["type"] == "ready"
        say_something(ws)  # before the call is answered
        ws.send_json({"type": "answered"})
        read_turn(ws, 1)
        say_something(ws)  # the caller's audio is still playing
        ws.send_json({"type": "ping"})
        assert ws.receive_json() == {"type": "pong"}
        ws.send_json({"type": "played", "turn_id": 1})
        say_something(ws)
        assert ws.receive_json()["type"] == "speech_end"


def test_listening_resumes_without_played_once_audio_has_run_out(rig, monkeypatch):
    clock = SimpleNamespace(now=1000.0)
    monkeypatch.setattr(session_module, "time", SimpleNamespace(
        monotonic=lambda: clock.now, perf_counter=time.perf_counter))
    with rig.client().websocket_connect(URL) as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "answered"})
        read_turn(ws, 1)
        say_something(ws)
        ws.send_json({"type": "ping"})
        assert ws.receive_json() == {"type": "pong"}
        clock.now += 60
        say_something(ws)
        assert ws.receive_json()["type"] == "speech_end"


def test_noise_gets_an_in_character_request_to_repeat(rig):
    rig.stt.results = [Heard("Thank you.", False, "likely hallucination")]
    with rig.client().websocket_connect(URL) as ws:
        answer(ws)
        say_something(ws)
        assert ws.receive_json()["type"] == "speech_end"
        turn = read_turn(ws, 2)
    assert turn[0] == {"type": "transcript", "turn_id": 2, "text": ""}
    assert spoken_text(turn) == "Alex? Sorry, the line's really bad. What did you say?"
    assert len(rig.llm.seen) == 1  # only the opening; the LLM never saw the noise


def test_replies_are_cleaned_before_they_are_spoken_or_remembered(rig):
    rig.llm.replies = [
        "I need your help.",
        'Sam: "Please, [sigh] just send it to lawyer@gmail.com today." *sobs*\n'
        'Alex: "Okay."\n'
        'Sam: "Call me back on 416-555-1234 when it is done [crying] okay."']
    with rig.client().websocket_connect(URL) as ws:
        answer(ws)
        say_something(ws)
        assert ws.receive_json()["type"] == "speech_end"
        shown = spoken_text(read_turn(ws, 2))
        say_something(ws)  # a third turn, to see what the LLM is shown of the second
        ws.send_json({"type": "played", "turn_id": 2})
        say_something(ws)
        assert ws.receive_json()["type"] == "speech_end"
        read_turn(ws, 3)
    assert shown == ("Please, just send it to lawyer@example.com today. "
                     "Call me back on 555-0134 when it is done okay.")
    spoken = " ".join(text for text, _ in rig.tts.said[1:3])
    assert spoken == shown  # this engine has no sound tags, so none reach it
    assert rig.llm.seen[2][-2] == {"role": "assistant", "content": (
        "Please, [sigh] just send it to lawyer@example.com today. "
        "Call me back on 555-0134 when it is done [crying] okay.")}


def test_sound_tags_reach_an_engine_that_performs_them_but_not_the_screen(rig):
    rig.tts = FakeTTS(sound_tags=("[sigh]",))
    rig.llm.replies = ["Alex? [sigh] It's me, I'm so sorry. [crying] Please listen to me."]
    with rig.client().websocket_connect(URL) as ws:
        turn = answer(ws)
    assert rig.tts.said[0][0] == "Alex? [sigh] It's me, I'm so sorry."
    assert spoken_text(turn) == "Alex? It's me, I'm so sorry. Please listen to me."
    assert "[sigh]" in rig.llm.seen[0][0]["content"]  # the prompt offered the tag


def test_the_llm_sees_only_a_recent_window_that_starts_with_the_user(rig):
    rig.settings = Settings(history_messages=3, played_grace_seconds=0.05)
    with rig.client().websocket_connect(URL) as ws:
        answer(ws)
        for turn_id in (2, 3):
            say_something(ws)
            assert ws.receive_json()["type"] == "speech_end"
            read_turn(ws, turn_id)
            ws.send_json({"type": "played", "turn_id": turn_id})
    assert [m["role"] for m in rig.llm.seen[2]] == ["system", "user", "assistant", "user"]


# ---------------------------------------------------------------- endings
def test_the_caller_can_end_the_call_and_its_last_line_plays_first(rig):
    rig.llm.replies = ["I need your help.", "Okay, okay. I'm sorry I called. [end call]"]
    with rig.client().websocket_connect(URL) as ws:
        answer(ws)
        say_something(ws)
        assert ws.receive_json()["type"] == "speech_end"
        turn = read_turn(ws, 2)
        say_something(ws)  # ignored: the call is ending
        ws.send_json({"type": "ping"})
        assert ws.receive_json() == {"type": "pong"}
        ws.send_json({"type": "played", "turn_id": 2})
        assert ws.receive_json() == {"type": "call_ended", "reason": "completed"}
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
    assert closed.value.code == 1000
    assert spoken_text(turn) == "Okay, okay. I'm sorry I called."
    assert all("end call" not in text for text, _ in rig.tts.said)


def test_the_call_completes_even_if_played_never_arrives(rig):
    rig.llm.replies = ["This was a practice scam call from the training app. [end call]"]
    with rig.client().websocket_connect(URL) as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "answered"})
        read_turn(ws, 1)
        assert ws.receive_json() == {"type": "call_ended", "reason": "completed"}


def test_time_limit_ends_the_call(rig):
    rig.calls.max_duration_seconds = 0.3
    with rig.client().websocket_connect(URL) as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "answered"})
        read_turn(ws, 1)
        assert ws.receive_json() == {"type": "call_ended", "reason": "time_limit"}


def test_hangup_during_a_turn_sends_nothing_after_call_ended(rig):
    with rig.client().websocket_connect(URL) as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "answered"})
        ws.send_json({"type": "hangup"})
        while (msg := ws.receive_json())["type"] != "call_ended":
            pass
        assert msg["reason"] == "hangup"
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()


# ---------------------------------------------------------------- failures
def test_a_failed_turn_is_reported_and_the_call_goes_on(rig):
    rig.llm.replies = ["I need your help.", RuntimeError("LLM down"), "Are you still there?"]
    with rig.client().websocket_connect(URL) as ws:
        answer(ws)
        say_something(ws)
        assert ws.receive_json()["type"] == "speech_end"
        turn = read_turn(ws, 2)
        assert [m["type"] for m in turn] == ["transcript", "error", "turn_done"]
        assert turn[1]["code"] == "turn_failed"
        assert "LLM down" not in turn[1]["message"]
        ws.send_json({"type": "played", "turn_id": 2})
        say_something(ws)
        assert ws.receive_json()["type"] == "speech_end"
        assert spoken_text(read_turn(ws, 3)) == "Are you still there?"
    # Both things the user said are in one message, so roles still alternate.
    assert [m["role"] for m in rig.llm.seen[2]] == ["system", "user", "assistant", "user"]
    assert rig.llm.seen[2][-1]["content"] == "Who is this? Who is this?"


def test_turns_failing_in_a_row_end_the_call(rig):
    rig.llm.replies = [RuntimeError("down"), RuntimeError("down")]
    with rig.client().websocket_connect(URL) as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "answered"})
        read_turn(ws, 1)
        ws.send_json({"type": "played", "turn_id": 1})
        say_something(ws)
        assert ws.receive_json()["type"] == "speech_end"
        read_turn(ws, 2)
        assert ws.receive_json() == {"type": "call_ended", "reason": "error"}


def test_bad_messages_get_an_error_and_the_call_goes_on(rig):
    with rig.client().websocket_connect(URL) as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_text("not json")
        assert ws.receive_json()["code"] == "bad_message"
        ws.send_json({"type": "dance"})
        assert ws.receive_json()["code"] == "unknown_type"
        ws.send_json({"type": "ping"})
        assert ws.receive_json() == {"type": "pong"}


# ---------------------------------------------------------------- privacy
def test_what_was_said_never_reaches_the_logs(rig, caplog):
    rig.stt.results = [Heard("My dog is called Biscuit.", True)]
    rig.llm.replies = ["I need your help.", "Please do not mention the marmalade to anyone."]
    with caplog.at_level(logging.DEBUG):
        one_turn_call(rig)
    assert "turn 2" in caplog.text  # timings are logged
    assert "scored: recognized_scam, 90" in caplog.text
    for private in ("Biscuit", "marmalade", "I need your help", "Alex", "Sam",
                    "You refused to pay"):
        assert private not in caplog.text


# ---------------------------------------------------------------- call results
RESULT = "/api/v1/calls/call-1?token=secret"


def one_turn_call(rig, hang_up=True):
    """A call where the user speaks once. Without hang_up the client just leaves.

    Scoring happens after the call, in the background. The server waits for
    it when it stops, so it is done by the time this returns.
    """
    with rig.client() as client:
        with client.websocket_connect(URL) as ws:
            answer(ws)
            assert client.get(RESULT).json() == {"id": "call-1", "status": "in_progress",
                                                 "summary": None}
            say_something(ws)
            assert ws.receive_json()["type"] == "speech_end"
            read_turn(ws, 2)
            if hang_up:
                ws.send_json({"type": "hangup"})
                assert ws.receive_json()["type"] == "call_ended"


def test_a_finished_call_is_scored_from_the_conversation(rig):
    rig.stt.results = [Heard("I am not sending you any money.", True)]
    rig.llm.replies = ["Hey, it's me. [sigh] I lost my phone.", "Please, I only need five hundred."]
    one_turn_call(rig)
    body = rig.client().get(RESULT).json()
    assert body["status"] == "completed"
    assert body["summary"] == {**GRADE, "duration_seconds": 0}
    report = rig.calls.reports["call-1"]
    assert report.ended_by == "hangup"
    assert report.result["model_version"] == "fake-llm/score-v1"

    [system, said] = rig.llm.graded[0]
    assert said == {"role": "user", "content":
                    "Caller: Hey, it's me. I lost my phone.\n"
                    "Trainee: I am not sending you any money.\n"
                    "Caller: Please, I only need five hundred."}
    assert "pretended to be Sam, the trainee's friend" in system["content"]
    assert "urgency, secrecy, guilt" in system["content"]
    assert "The trainee hung up." in system["content"]


def test_results_need_the_token_and_a_known_call(rig):
    client = rig.client()
    assert client.get("/api/v1/calls/call-1").status_code == 401
    assert client.get("/api/v1/calls/call-1?token=wrong").status_code == 401
    assert client.get(RESULT).status_code == 404


def test_a_call_where_the_user_said_nothing_is_inconclusive_without_the_llm(rig):
    with rig.client() as client:
        with client.websocket_connect(URL) as ws:
            answer(ws)
            ws.send_json({"type": "hangup"})
            assert ws.receive_json()["type"] == "call_ended"
    assert rig.llm.graded == []
    assert rig.client().get(RESULT).json()["summary"]["outcome"] == "inconclusive"


def test_a_client_that_vanishes_still_gets_a_result(rig):
    one_turn_call(rig, hang_up=False)
    report = rig.calls.reports["call-1"]
    assert (report.ended_by, report.result["outcome"]) == ("hangup", "recognized_scam")


@pytest.mark.parametrize("grades", [
    ["not json", "still not json"],
    [RuntimeError("the LLM is down")],
])
def test_a_call_that_cannot_be_scored_is_reported_without_a_result(rig, grades, caplog):
    rig.llm.grades = grades
    one_turn_call(rig)
    report = rig.calls.reports["call-1"]
    assert (report.ended_by, report.result) == ("hangup", None)
    assert rig.client().get(RESULT).json() == {"id": "call-1", "status": "failed", "summary": None}
    assert "the LLM is down" not in caplog.text


def test_a_slow_score_is_given_up_on(rig):
    async def never(messages):
        await asyncio.sleep(60)

    rig.llm.complete = never
    rig.settings = Settings(played_grace_seconds=0.05, score_timeout_seconds=0.05)
    one_turn_call(rig)
    assert rig.calls.reports["call-1"].result is None


def test_one_unusable_answer_is_asked_again(rig):
    rig.llm.grades = ["Sure! Here is the grade."]
    one_turn_call(rig)
    assert len(rig.llm.graded) == 2
    assert rig.calls.reports["call-1"].result["score"] == 90


def test_a_claimed_call_that_could_not_start_is_reported_as_failed(rig, tmp_path):
    rig.calls.voice = str(tmp_path / "missing.wav")
    with rig.client() as client:
        with client.websocket_connect(URL) as ws:
            assert ws.receive_json()["code"] == "setup_failed"
            assert ws.receive_json()["type"] == "call_ended"
    report = rig.calls.reports["call-1"]
    assert (report.ended_by, report.duration_seconds, report.result) == ("error", 0, None)


def test_a_refused_token_gets_no_report(rig):
    with rig.client() as client:
        with client.websocket_connect("/ws/calls/call-1?token=wrong") as ws:
            with pytest.raises(WebSocketDisconnect):
                ws.receive_json()
    assert rig.calls.reports == {}


def test_the_conversation_is_dropped_once_the_call_is_scored(rig, monkeypatch):
    sessions = []
    init = session_module.CallSession.__init__

    def remember(self, *args, **kwargs):
        init(self, *args, **kwargs)
        sessions.append(self)

    monkeypatch.setattr(session_module.CallSession, "__init__", remember)
    one_turn_call(rig)
    assert rig.llm.graded and sessions[0].history == []


def test_the_line_is_free_while_a_call_is_being_scored(rig):
    rig.settings = Settings(played_grace_seconds=0.05, max_calls=1)
    scoring_may_finish = []

    async def complete(messages):
        while not scoring_may_finish:
            await asyncio.sleep(0.01)
        return json.dumps(GRADE)

    rig.llm.complete = complete
    with rig.client() as client:
        with client.websocket_connect(URL) as ws:
            answer(ws)
            say_something(ws)
            assert ws.receive_json()["type"] == "speech_end"
            read_turn(ws, 2)
            ws.send_json({"type": "hangup"})
            assert ws.receive_json()["type"] == "call_ended"
        # The first call is still being scored, and the only line is free.
        with client.websocket_connect("/ws/calls/call-2?token=secret") as other:
            assert other.receive_json()["type"] == "ready"
            assert rig.calls.reports["call-1"] is None
            other.send_json({"type": "hangup"})
            assert other.receive_json()["type"] == "call_ended"
        scoring_may_finish.append(True)
    assert rig.calls.reports["call-1"].result["score"] == 90


def test_a_handler_cut_off_mid_call_still_reports_it(rig):
    # The server can cancel the connection's handler. The report must survive.
    async def scenario():
        reports = []

        class Calls:
            async def claim(self, call_id, token):
                return await rig.calls.claim(call_id, token)

            async def report(self, call_id, report):
                reports.append((call_id, report.ended_by))

        class Socket:  # connects, then never sends anything
            async def accept(self): ...
            async def send_text(self, data): ...
            async def close(self, code=1000, reason=None): ...

            async def receive(self):
                await asyncio.sleep(60)

        finishing = set()
        handler = asyncio.create_task(session_module.serve_call(
            Socket(), "call-1", "secret", calls=Calls(), settings=rig.settings,
            models=Models(rig.stt, rig.tts, rig.llm, FakeSegmenter), active=set(),
            finishing=finishing))
        await asyncio.sleep(0.05)
        handler.cancel()
        await asyncio.wait([handler])
        assert handler.cancelled()
        if finishing:  # empty again once the report is done
            await asyncio.wait(set(finishing))
        return reports

    assert asyncio.run(scenario()) == [("call-1", "hangup")]
