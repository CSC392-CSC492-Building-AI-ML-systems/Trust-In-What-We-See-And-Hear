"""One call over the WebSocket described in PROTOCOL.md.

Same messages as mock_server.py, with real models behind them:

    microphone -> segmenter -> speech to text -> LLM -> chunker -> voice

The conversation lives in `CallSession.history`, in memory, for the length of
the call. Nothing here writes it to disk or to the logs: the app does not
keep transcripts. Logs carry timings only. When the call ends it is scored
from that conversation (scoring.py), the result is reported, and the
conversation is cleared.

AI-assisted code (written with Claude Code), cited per the CSC398 AI policy.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import math
import time
import wave
from contextlib import aclosing, asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException, WebSocket
from starlette.websockets import WebSocketDisconnect

from .calls import CallReport, CallsUnavailable, VoiceError, fetch_voice, wav_seconds
from .chunker import SpeechChunker, strip_tags
from .config import Settings
from .context import CallContext, CallContextValidationError
from .models import Models
from .parts import Piece, Utterance
from .prompt import OPENING_NOTE, build_system_prompt, hint_words, prompt_safe
from .reply_filter import clean_reply, names_and_first_names, safe_destinations, split_end
from .scoring import score_call

log = logging.getLogger("pipeline.call")
IN_RATE = 16_000  # microphone audio the client sends


def wav_b64(piece: Piece) -> str:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(piece.sample_rate)
        w.writeframes(piece.pcm16)
    return base64.b64encode(buf.getvalue()).decode()


async def _just(text: str) -> AsyncIterator[str]:
    yield text


class CallSession:
    def __init__(self, ws: WebSocket, call_id: str, ctx: CallContext, voice: Any,
                 models: Models, settings: Settings, max_seconds: float) -> None:
        self.ws = ws
        self.id = call_id
        self.ctx = ctx
        self.answered_at: float | None = None
        self.voice = voice  # this call's voice handle; dropped when the call ends
        self.models = models
        self.settings = settings
        self.max_seconds = max_seconds
        self.sound_tags = models.tts.info.sound_tags
        self.system_prompt = build_system_prompt(ctx, self.sound_tags)
        self.hint_words = hint_words(ctx)
        self.persona_labels = names_and_first_names(ctx.persona.name)
        self.other_labels = names_and_first_names(ctx.target.called_as, ctx.target.name)
        called = prompt_safe(ctx.target.called_as or ctx.target.name)
        # Said in character when speech to text could not make out the user.
        self.reprompt = (f"{called}? " if called else "") + \
            "Sorry, the line's really bad. What did you say?"
        self.segmenter = models.new_segmenter()
        self.history: list[dict] = []  # the conversation; memory only
        self.turns = 0
        self.turn: asyncio.Task | None = None  # the caller turn being sent
        self.timer: asyncio.Task | None = None  # set once the call is answered
        self.finish: asyncio.Task | None = None  # waits for the last turn to play
        self.wait_played: int | None = None
        self.listen_at = 0.0
        self.played = asyncio.Event()
        self.ending = False  # the caller said its last line
        self.failures = 0  # turns failed in a row

    async def send(self, type: str, **fields) -> None:
        await self.ws.send_text(json.dumps({"type": type, **fields}))

    def listening(self) -> bool:
        if self.timer is None or self.turn is not None or self.ending:
            return False
        # After a turn, wait for `played`, or for the audio to have run out.
        return self.wait_played is None or time.monotonic() >= self.listen_at

    # ------------------------------------------------------------ turns
    def start_turn(self, utterance: Utterance | None = None) -> None:
        self.turns += 1
        self.played.clear()
        self.turn = asyncio.create_task(self.run_turn(self.turns, utterance))

    async def run_turn(self, turn_id: int, utterance: Utterance | None) -> None:
        """One caller turn. `utterance` is None for the opening line."""
        started = time.perf_counter()
        started_at = time.monotonic()
        self.first_audio_at: float | None = None
        self.spoken_seconds = 0.0
        stt_ms = None
        try:
            if utterance is None:
                chunks = self.reply_chunks(OPENING_NOTE)
            else:
                heard = await self.models.transcribe(utterance, self.hint_words)
                stt_ms = round((time.perf_counter() - started) * 1000)
                await self.send("transcript", turn_id=turn_id,
                                text=heard.text if heard.accepted else "")
                if heard.accepted:
                    chunks = self.reply_chunks(heard.text)
                else:
                    # Noise, or a phrase the model likely made up. Do not
                    # answer nonsense: ask again, like on a bad line.
                    self.history.append({"role": "assistant", "content": self.reprompt})
                    chunks = _just(self.reprompt)
            await self.say(turn_id, chunks)
            self.failures = 0
        except WebSocketDisconnect:
            raise
        except Exception:
            self.failures += 1
            log.exception("call %s turn %s failed", self.id, turn_id)
            await self.send("error", code="turn_failed",
                            message="The caller could not reply. Try speaking again.")
        await self.send("turn_done", turn_id=turn_id)

        now = time.monotonic()
        heard_until = now if self.first_audio_at is None else \
            max(now, self.first_audio_at + self.spoken_seconds)
        self.wait_played = turn_id
        self.listen_at = heard_until + self.settings.played_grace_seconds
        self.segmenter.reset()  # forget anything picked up while the caller spoke
        first_ms = None if self.first_audio_at is None else \
            round((self.first_audio_at - started_at) * 1000)
        log.info("call %s turn %s: stt %s ms, first audio %s ms, total %s ms, %.1f s of audio",
                 self.id, turn_id, stt_ms, first_ms,
                 round((time.perf_counter() - started) * 1000), self.spoken_seconds)

    def clean(self, text: str) -> str:
        return safe_destinations(clean_reply(text, self.persona_labels, self.other_labels))

    def llm_messages(self) -> list[dict]:
        window = self.history[-self.settings.history_messages:]
        while window and window[0]["role"] != "user":
            window = window[1:]
        # Two caller lines in a row happen when the caller asks the user to
        # repeat. Some chat formats need the roles to alternate, so join them.
        messages = [{"role": "system", "content": self.system_prompt}]
        for message in window:
            if message["role"] == messages[-1]["role"]:
                messages[-1] = {**messages[-1],
                                "content": messages[-1]["content"] + " " + message["content"]}
            else:
                messages.append(message)
        return messages

    async def reply_chunks(self, user_text: str) -> AsyncIterator[str]:
        """Ask the LLM for the caller's reply; yield it in speakable chunks."""
        if self.history and self.history[-1]["role"] == "user":
            # The last turn failed before the caller answered. Keep roles alternating.
            self.history[-1]["content"] += " " + user_text
        else:
            self.history.append({"role": "user", "content": user_text})
        chunker = SpeechChunker()
        full = ""
        async with aclosing(self.models.llm.stream(self.llm_messages())) as deltas:
            async for delta in deltas:
                full += delta
                for chunk in chunker.feed(delta):
                    yield chunk
        for chunk in chunker.flush():
            yield chunk
        text, ended = split_end(self.clean(full))
        if text:
            # Store the cleaned reply so the model does not see (and copy) labels.
            self.history.append({"role": "assistant", "content": text})
        elif not ended:
            raise RuntimeError("the LLM returned nothing to say")
        self.ending = self.ending or ended

    async def say(self, turn_id: int, chunks: AsyncIterator[str]) -> None:
        """Voice text chunks and send the audio pieces."""
        seq = 0
        async with aclosing(chunks) as chunks:
            async for chunk in chunks:
                spoken = strip_tags(split_end(self.clean(chunk))[0], keep=self.sound_tags)
                shown = strip_tags(spoken)
                if not shown:
                    continue
                first = True
                speech = self.models.speak(spoken, self.voice, first_in_turn=seq == 0)
                async with aclosing(speech) as pieces:
                    async for piece in pieces:
                        if not piece.pcm16:
                            continue
                        await self.send("audio", turn_id=turn_id, seq=seq,
                                        text=shown if first else "", audio=wav_b64(piece))
                        if self.first_audio_at is None:
                            self.first_audio_at = time.monotonic()
                        self.spoken_seconds += piece.seconds
                        first = False
                        seq += 1

    async def until_played(self) -> None:
        """Let the caller's last line play before the call ends."""
        try:
            await asyncio.wait_for(self.played.wait(),
                                   max(0.0, self.listen_at - time.monotonic()))
        except asyncio.TimeoutError:
            pass

    # ------------------------------------------------------------ messages
    async def handle(self, frame: dict) -> bool:
        """Process one client frame. Returns True if the client hung up."""
        if frame.get("bytes") is not None:
            if self.listening():
                for utterance in self.segmenter.feed(frame["bytes"]):
                    await self.send("speech_end", seconds=round(utterance.seconds, 2))
                    self.start_turn(utterance)
                    break
            return False
        try:
            msg = json.loads(frame.get("text") or "")
            kind = msg["type"]
        except (ValueError, TypeError, KeyError):
            await self.send("error", code="bad_message",
                            message="expected a JSON object with a type")
            return False
        if kind == "answered":
            if self.timer is None:
                self.answered_at = time.monotonic()
                self.timer = asyncio.create_task(asyncio.sleep(self.max_seconds))
                self.start_turn()
        elif kind == "played":
            if msg.get("turn_id") == self.wait_played:
                self.wait_played = None
                self.played.set()
                self.segmenter.reset()
        elif kind == "hangup":
            return True
        elif kind == "ping":
            await self.send("pong")
        else:
            await self.send("error", code="unknown_type", message="unknown message type")
        return False

    async def run(self) -> str | None:
        """Run the call. Returns why it ended, or None if the client vanished."""
        await self.send("ready", call_id=self.id, input_sample_rate=IN_RATE,
                        output_sample_rate=self.models.tts.info.sample_rate,
                        max_duration_seconds=round(self.max_seconds))
        recv = asyncio.create_task(self.ws.receive())
        # Runs out if the client never answers; the call timer takes over
        # once it does.
        unanswered = asyncio.create_task(asyncio.sleep(self.settings.answer_timeout_seconds))
        reason = None
        try:
            while reason is None:
                tasks = {t for t in (recv, self.timer, self.turn, self.finish)
                         if t is not None}
                if self.timer is None:
                    tasks.add(unanswered)
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                if self.turn in done:
                    self.turn.result()
                    self.turn = None
                    if self.failures >= self.settings.max_failed_turns:
                        reason = "error"
                    elif self.ending:
                        self.finish = asyncio.create_task(self.until_played())
                if reason is None and self.finish in done:
                    reason = "completed"
                if reason is None and self.timer in done:
                    reason = "time_limit"
                if reason is None and recv in done:
                    frame = recv.result()
                    if frame["type"] == "websocket.disconnect":
                        return None
                    if await self.handle(frame):
                        reason = "hangup"
                    else:
                        recv = asyncio.create_task(self.ws.receive())
                if reason is None and self.timer is None and unanswered.done():
                    reason = "time_limit"
        finally:
            tasks = [t for t in (recv, unanswered, self.timer, self.turn, self.finish)
                     if t is not None]
            for task in tasks:
                task.cancel()
            # Let a cancelled turn stop so nothing is sent after call_ended
            # and the GPU is free again.
            await asyncio.wait(tasks)
            for task in tasks:
                if not task.cancelled():
                    task.exception()  # looked at, so asyncio does not warn
        await self.send("call_ended", reason=reason)
        await self.ws.close(code=1000)
        return reason


async def load_voice(source: str, models: Models) -> Any:
    """Fetch the persona's recording and turn it into this call's voice."""
    path, temporary = await fetch_voice(source)
    try:
        need = models.tts.info.min_reference_seconds
        if wav_seconds(path) < need:
            raise VoiceError(f"voice recording is shorter than {need} s")
        return await models.prepare_voice(str(path))
    finally:
        if temporary:
            path.unlink(missing_ok=True)


async def serve_call(ws: WebSocket, call_id: str, token: str, *, calls: Any,
                     models: Models, settings: Settings, active: set,
                     finishing: set) -> None:
    await ws.accept()
    if not token:
        await ws.close(code=4401, reason="missing or invalid token")
        return

    async def refuse(code: str, message: str) -> None:
        await ws.send_text(json.dumps({"type": "error", "code": code, "message": message}))
        await ws.send_text(json.dumps({"type": "call_ended", "reason": "error"}))
        await ws.close(code=1000)

    # Checked before the token: a token can be claimed only once, so it must
    # not be spent on a call there is no room for.
    if len(active) >= settings.max_calls:
        await refuse("busy", "All lines are busy. Try again in a few minutes.")
        return
    slot = object()
    active.add(slot)
    session: CallSession | None = None
    ended_by: str | None = None  # set once the call is claimed: it then gets a report
    try:
        try:
            setup = await calls.claim(call_id, token)
        except CallsUnavailable as e:
            log.error("call %s not started: %s", call_id, e)
            await refuse("setup_failed", "This call could not be set up.")
            return
        if setup is None:
            await ws.close(code=4401, reason="missing or invalid token")
            return
        ended_by = "error"
        try:
            ctx, warnings = CallContext.from_dict(setup.context)
            limit = setup.max_duration_seconds
            if not (math.isfinite(limit) and limit > 0):
                raise VoiceError("the call's time limit is not a positive number")
            voice = await load_voice(setup.voice, models)
            session = CallSession(ws, call_id, ctx, voice, models, settings,
                                  min(limit, settings.max_call_seconds))
        except (CallContextValidationError, VoiceError) as e:
            log.error("call %s not started: %s", call_id, e)
            await refuse("setup_failed", "This call could not be set up.")
            return
        except Exception:
            log.exception("call %s not started", call_id)
            await refuse("setup_failed", "This call could not be set up.")
            return
        for warning in warnings:
            log.warning("call %s context: %s", call_id, warning)
        ended_by = "hangup"  # what a client that just disconnects counts as
        reason = await session.run()
        ended_by = reason or ended_by
        log.info("call %s ended: %s, %s caller turns", call_id,
                 reason or "client disconnected", session.turns)
    except WebSocketDisconnect:
        log.info("call %s: client disconnected", call_id)
    finally:
        active.discard(slot)  # scoring needs no line, so the next call can start
        if ended_by is not None:
            # A task of its own: this handler can be cancelled when the
            # connection goes away, and the result must not go with it.
            task = asyncio.create_task(wrap_up(
                call_id, session, ended_by, calls=calls, models=models, settings=settings))
            finishing.add(task)
            task.add_done_callback(finishing.discard)


async def wrap_up(call_id: str, session: CallSession | None, ended_by: str, *,
                  calls: Any, models: Models, settings: Settings) -> None:
    """Score the call and report it. After this the conversation is gone."""
    result = None
    seconds = 0
    if session is not None:
        if session.answered_at is not None:
            seconds = round(time.monotonic() - session.answered_at)
        try:
            result = await asyncio.wait_for(
                score_call(models.llm, session.ctx, session.history, ended_by, call_id),
                settings.score_timeout_seconds)
        except Exception as e:
            # The error's type only: its text could quote the conversation.
            log.error("call %s could not be scored: %s", call_id, type(e).__name__)
        session.history.clear()
    try:
        await calls.report(call_id, CallReport(ended_by, seconds, result))
    except Exception as e:
        log.error("call %s could not be reported: %s", call_id, type(e).__name__)


def create_app(models: Models, calls: Any, settings: Settings) -> FastAPI:
    active: set = set()
    finishing: set = set()  # calls that ended and are being scored and reported

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        if finishing:  # let them finish before the server stops
            await asyncio.wait(set(finishing), timeout=(
                settings.score_timeout_seconds + calls.report_budget_seconds + 5))
        if hasattr(calls, "aclose"):
            await calls.aclose()

    app = FastAPI(title="Call audio server", lifespan=lifespan)

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "calls": len(active), "voice_engine": models.tts.info.name}

    if hasattr(calls, "reports"):
        # Without a backend (calls.DevCalls): serve results the way the
        # backend will, so a call can be checked from start to result.
        @app.get("/api/v1/calls/{call_id}")
        def call_details(call_id: str, token: str = "") -> dict:
            if not calls.accepts(token):
                raise HTTPException(status_code=401, detail="Missing or invalid token")
            if call_id not in calls.reports:
                raise HTTPException(status_code=404, detail="Call not found")
            report = calls.reports[call_id]
            if report is None:
                return {"id": call_id, "status": "in_progress", "summary": None}
            if report.result is None or report.ended_by == "error":
                return {"id": call_id, "status": "failed", "summary": None}
            summary = {k: v for k, v in report.result.items() if k != "model_version"}
            return {"id": call_id, "status": "completed",
                    "summary": {**summary, "duration_seconds": report.duration_seconds}}

    @app.websocket("/ws/calls/{call_id}")
    async def call_socket(ws: WebSocket, call_id: str, token: str = "") -> None:
        await serve_call(ws, call_id, token, calls=calls, models=models,
                         settings=settings, active=active, finishing=finishing)

    return app
