"""The loaded models, shared by every call, behind async methods.

The speech models are ordinary blocking code. This class runs them in worker
threads and makes sure only one of them uses the GPU at a time. That lock
also protects voices: an engine holds one voice at a time while it speaks,
so two calls must never speak at once.

The models are passed in already loaded (server.py does that), so tests can
pass fakes and still run this code.

AI-assisted code (written with Claude Code), cited per the CSC398 AI policy.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any, AsyncIterator, Callable

from .parts import Heard, Piece, TTSEngine, Utterance


class Models:
    def __init__(self, stt: Any, tts: TTSEngine, llm: Any,
                 new_segmenter: Callable[[], Any]):
        self.stt = stt  # .transcribe(audio, hint_words) -> Heard
        self.tts = tts
        self.llm = llm  # .stream(messages) -> async iterator of text
        # Makes the object that cuts one call's microphone stream into
        # utterances: .feed(bytes) -> list[Utterance], .reset()
        self.new_segmenter = new_segmenter
        self.gpu = asyncio.Lock()

    async def _on_gpu(self, fn: Callable, *args):
        async with self.gpu:
            job = asyncio.get_running_loop().run_in_executor(None, fn, *args)
            try:
                return await asyncio.shield(job)
            except asyncio.CancelledError:
                # The thread cannot be stopped. Keep the lock until it is done.
                await asyncio.wait([job])
                raise

    async def transcribe(self, utterance: Utterance, hint_words: str) -> Heard:
        return await self._on_gpu(self.stt.transcribe, utterance.audio, hint_words)

    async def prepare_voice(self, wav_path: str) -> Any:
        return await self._on_gpu(self.tts.prepare_voice, wav_path)

    async def speak(self, text: str, voice: Any, first_in_turn: bool) -> AsyncIterator[Piece]:
        """Yield audio pieces as the engine makes them.

        Holds the GPU for the whole text. Close the iterator (contextlib.
        aclosing) if you stop early: it then waits for the engine to stop
        before letting another call speak.
        """
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        stop = threading.Event()

        def work() -> None:
            try:
                for piece in self.tts.speak(text, voice, first_in_turn):
                    if stop.is_set():
                        break
                    loop.call_soon_threadsafe(queue.put_nowait, piece)
            except Exception as e:  # hand the error to the turn
                loop.call_soon_threadsafe(queue.put_nowait, e)
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        async with self.gpu:
            worker = loop.run_in_executor(None, work)
            try:
                while (item := await queue.get()) is not None:
                    if isinstance(item, Exception):
                        raise item
                    yield item
            finally:
                stop.set()
                await asyncio.wait([worker])
