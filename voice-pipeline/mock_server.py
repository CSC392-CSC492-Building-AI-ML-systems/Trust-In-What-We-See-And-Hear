"""Mock of the call audio WebSocket described in PROTOCOL.md.

No GPU and no models: speech is detected by loudness, the "transcript" is a
placeholder, and the caller's voice is a tone. It exists so the frontend can
build the call screen against the real message flow.

Settings (environment variables):
    MOCK_MAX_SECONDS        call time limit, default 300
    MOCK_END_AFTER_TURNS    end with reason "completed" after this many caller
                            turns, default 0 (never)
    MOCK_CHUNK_DELAY        seconds of fake processing before each message,
                            default 0.4
    MOCK_END_SILENCE_MS     silence that ends the user's turn, default 600
    MOCK_SPEECH_RMS         loudness (0 to 32767) that counts as speech,
                            default 500

AI-assisted code (written with Claude Code), cited per the CSC398 AI policy.
"""

import array
import asyncio
import base64
import io
import json
import math
import os
import sys
import time
import wave

from fastapi import FastAPI, WebSocket

IN_RATE = 16_000  # microphone audio the client sends
OUT_RATE = 24_000  # caller audio the server sends
MAX_SECONDS = float(os.environ.get("MOCK_MAX_SECONDS", "300"))
END_AFTER_TURNS = int(os.environ.get("MOCK_END_AFTER_TURNS", "0"))
CHUNK_DELAY = float(os.environ.get("MOCK_CHUNK_DELAY", "0.4"))
END_SILENCE_MS = int(os.environ.get("MOCK_END_SILENCE_MS", "600"))
SPEECH_RMS = int(os.environ.get("MOCK_SPEECH_RMS", "500"))
MIN_SPEECH_MS = 300  # shorter sounds are treated as noise

OPENING = ["Hey, it's me. This is the mock caller.", "Can you hear me okay?"]
REPLIES = [
    ["Listen, I'm in a bit of trouble.", "I need your help with something today."],
    ["I can't really explain right now.", "Can you just trust me on this?"],
    ["Please don't tell anyone yet.", "I'll pay you back, I promise."],
]


def tone_wav_b64(seconds: float) -> str:
    """A low wobbling tone as a base64 WAV, standing in for the caller's voice."""
    n = int(seconds * OUT_RATE)
    fade = OUT_RATE // 100  # 10 ms, avoids clicks between pieces
    samples = array.array("h", bytes(2 * n))
    for i in range(n):
        t = i / OUT_RATE
        level = 0.6 + 0.4 * math.sin(2 * math.pi * 4 * t)
        edge = min(1.0, i / fade, (n - 1 - i) / fade)
        samples[i] = int(8000 * level * edge * math.sin(2 * math.pi * 180 * t))
    if sys.byteorder == "big":
        samples.byteswap()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(OUT_RATE)
        w.writeframes(samples.tobytes())
    return base64.b64encode(buf.getvalue()).decode()


class Segmenter:
    """Finds the end of the user's speech in a 16 kHz PCM16 stream by loudness.

    The real pipeline uses a speech detector model; this is only good enough
    for a quiet room.
    """

    WINDOW_MS = 20
    WINDOW_BYTES = IN_RATE * WINDOW_MS // 1000 * 2

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.buf = b""
        self.speech_ms = 0
        self.silence_ms = 0

    def feed(self, data: bytes) -> float | None:
        """Add audio. Returns seconds of speech when an utterance just ended."""
        self.buf += data
        while len(self.buf) >= self.WINDOW_BYTES:
            window = array.array("h", self.buf[: self.WINDOW_BYTES])
            self.buf = self.buf[self.WINDOW_BYTES :]
            if sys.byteorder == "big":
                window.byteswap()
            rms = math.sqrt(sum(s * s for s in window) / len(window))
            if rms >= SPEECH_RMS:
                self.speech_ms += self.WINDOW_MS
                self.silence_ms = 0
            elif self.speech_ms:
                self.silence_ms += self.WINDOW_MS
            if self.silence_ms >= END_SILENCE_MS:
                heard = self.speech_ms
                self.reset()
                if heard >= MIN_SPEECH_MS:
                    return heard / 1000
        return None


class Call:
    def __init__(self, ws: WebSocket, call_id: str) -> None:
        self.ws = ws
        self.id = call_id
        self.segmenter = Segmenter()
        self.turns = 0
        self.turn: asyncio.Task | None = None  # the caller turn being sent
        self.timer: asyncio.Task | None = None  # set once the call is answered
        self.wait_played: int | None = None
        self.listen_at = 0.0

    async def send(self, type: str, **fields) -> None:
        await self.ws.send_text(json.dumps({"type": type, **fields}))

    def listening(self) -> bool:
        if self.timer is None or self.turn is not None:
            return False
        # After a turn, wait for `played`, or for the audio to have run out.
        return self.wait_played is None or time.monotonic() >= self.listen_at

    def start_turn(self, lines: list[str], heard: float | None = None) -> None:
        self.turns += 1
        self.turn = asyncio.create_task(self.speak(self.turns, lines, heard))

    async def speak(self, turn_id: int, lines: list[str], heard: float | None) -> None:
        if heard is not None:
            await asyncio.sleep(CHUNK_DELAY)
            await self.send("transcript", turn_id=turn_id,
                            text=f"(mock) heard {heard:.1f} s of speech")
        total = 0.0
        for seq, text in enumerate(lines):
            await asyncio.sleep(CHUNK_DELAY)
            seconds = max(0.6, len(text) * 0.06)
            total += seconds
            await self.send("audio", turn_id=turn_id, seq=seq, text=text,
                            audio=tone_wav_b64(seconds))
        await self.send("turn_done", turn_id=turn_id)
        self.wait_played = turn_id
        self.listen_at = time.monotonic() + total + 1.0
        self.segmenter.reset()

    async def handle(self, frame: dict) -> bool:
        """Process one client frame. Returns True if the client hung up."""
        if frame.get("bytes") is not None:
            if self.listening():
                heard = self.segmenter.feed(frame["bytes"])
                if heard is not None:
                    await self.send("speech_end", seconds=round(heard, 2))
                    self.start_turn(REPLIES[(self.turns - 1) % len(REPLIES)], heard)
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
                self.timer = asyncio.create_task(asyncio.sleep(MAX_SECONDS))
                self.start_turn(OPENING)
        elif kind == "played":
            if msg.get("turn_id") == self.wait_played:
                self.wait_played = None
                self.segmenter.reset()
        elif kind == "hangup":
            return True
        elif kind == "ping":
            await self.send("pong")
        else:
            await self.send("error", code="unknown_type", message="unknown message type")
        return False

    async def run(self) -> None:
        await self.send("ready", call_id=self.id, input_sample_rate=IN_RATE,
                        output_sample_rate=OUT_RATE, max_duration_seconds=MAX_SECONDS)
        recv = asyncio.create_task(self.ws.receive())
        reason = None
        try:
            while reason is None:
                tasks = {t for t in (recv, self.timer, self.turn) if t is not None}
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                if self.turn in done:
                    self.turn.result()
                    self.turn = None
                    if END_AFTER_TURNS and self.turns >= END_AFTER_TURNS:
                        reason = "completed"
                if reason is None and self.timer in done:
                    reason = "time_limit"
                if reason is None and recv in done:
                    frame = recv.result()
                    if frame["type"] == "websocket.disconnect":
                        return
                    if await self.handle(frame):
                        reason = "hangup"
                    else:
                        recv = asyncio.create_task(self.ws.receive())
        finally:
            for task in (recv, self.timer, self.turn):
                if task is not None and not task.done():
                    task.cancel()
        if self.turn is not None:
            # Let the cancelled turn finish so nothing is sent after call_ended.
            await asyncio.gather(self.turn, return_exceptions=True)
        await self.send("call_ended", reason=reason)
        await self.ws.close(code=1000)


app = FastAPI(title="Mock call audio server")


@app.websocket("/ws/calls/{call_id}")
async def call_socket(ws: WebSocket, call_id: str, token: str = "") -> None:
    await ws.accept()
    if not token:
        await ws.close(code=4401, reason="missing token")
        return
    await Call(ws, call_id).run()
