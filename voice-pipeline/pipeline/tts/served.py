"""Voice engines that run as their own server next to the call server.

    TTS_ENGINE=ming    Ming-3.0-tts-0.5B (inclusionAI), behind pod/ming_server.py
    TTS_ENGINE=drama   Fish Audio Drama 3, behind a vLLM-Omni server

Both clone a voice from a recording plus the words said in it, so preparing
a voice transcribes the recording with the call server's own speech to text.
The recording and its words are sent with every request and kept only in
this call's voice handle, as with Chatterbox (parts.TTSEngine). Neither is
logged.

Ming makes whole sentences, so the caller starts speaking once the first
sentence is ready. Drama streams: vLLM-Omni sends audio as it is made, and
the pieces go out as they arrive, so the caller starts sooner. Audio is
converted to 24 kHz, the rate the call protocol sends.

Drama can keep going after the words end (silence, or noise, until its token
limit). Each sentence is therefore stopped after a second of silence once
speech has started, or at a length cap from its word count, whichever comes
first.

    MING_URL                  default http://127.0.0.1:8095
    DRAMA_URL                 default http://127.0.0.1:8091/v1/audio/speech
    DRAMA_STREAM              1 (default) to stream, 0 for whole sentences
    DRAMA_SECONDS_PER_WORD    length cap per word, default 0.6 (plus 2 s)
    DRAMA_STOP_SILENCE        seconds of silence that end a sentence, default 1.0

AI-assisted code (written with Claude Code), cited per the CSC398 AI policy.
"""

from __future__ import annotations

import base64
import io
import itertools
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

import httpx
import numpy as np

from ..parts import EngineInfo, Piece

RATE = 24_000  # what the call protocol sends (PROTOCOL.md)
log = logging.getLogger(__name__)


@dataclass
class Reference:
    """One call's voice: the recording and the words said in it."""
    wav: bytes
    text: str


def to_pcm16(wav: bytes, rate: int = RATE) -> bytes:
    """Any WAV the engines return, as mono 16-bit PCM at `rate`."""
    import soundfile

    audio, source_rate = soundfile.read(io.BytesIO(wav), dtype="float32", always_2d=True)
    audio = audio.mean(axis=1)
    if source_rate != rate and len(audio):
        try:
            import torch
            import torchaudio

            audio = torchaudio.functional.resample(
                torch.from_numpy(audio), source_rate, rate).numpy()
        except ImportError:
            steps = np.arange(0, len(audio), source_rate / rate)
            audio = np.interp(steps, np.arange(len(audio)), audio).astype(np.float32)
    return (np.clip(audio, -1.0, 1.0) * 32767).astype("<i2").tobytes()


class StreamResampler:
    """Converts a PCM stream to the protocol's rate, chunk by chunk, with no
    clicks where chunks meet: a low-pass filter against aliasing, then linear
    interpolation, both carrying their state from one chunk to the next."""

    TAPS = 63

    def __init__(self, source_rate: int, rate: int = RATE):
        self.step = source_rate / rate
        self.filter = None
        if source_rate > rate:  # keep only what the lower rate can hold
            cutoff = 0.45 * rate / source_rate
            n = np.arange(self.TAPS) - (self.TAPS - 1) / 2
            taps = 2 * cutoff * np.sinc(2 * cutoff * n) * np.blackman(self.TAPS)
            self.filter = (taps / taps.sum()).astype(np.float32)
        self.tail = np.zeros(self.TAPS - 1, dtype=np.float32)
        self.prev = 0.0  # last filtered sample, for interpolating across chunks
        self.pos = 0.0  # time of the next output sample, in source samples from the chunk start

    def feed(self, samples: np.ndarray) -> np.ndarray:
        """Float samples at the source rate in, float samples at `rate` out."""
        if not len(samples):
            return np.zeros(0, dtype=np.float32)
        if self.filter is not None:
            joined = np.concatenate([self.tail, samples])
            self.tail = joined[-(self.TAPS - 1):]
            samples = np.convolve(joined, self.filter, mode="valid").astype(np.float32)
        n = len(samples)
        last = n - 1
        if self.pos > last:
            times = np.zeros(0)
        else:
            times = self.pos + self.step * np.arange(int((last - self.pos) // self.step) + 1)
        points = np.concatenate([[self.prev], samples])
        out = np.interp(times + 1, np.arange(n + 1), points).astype(np.float32)
        self.pos = (times[-1] + self.step if len(times) else self.pos) - n
        self.prev = float(samples[-1])
        return out


def pcm16_to_float(pcm: bytes) -> np.ndarray:
    return np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype="<i2").astype(np.float32) / 32768


def float_to_pcm16(audio: np.ndarray) -> bytes:
    return (np.clip(audio, -1.0, 1.0) * 32767).astype("<i2").tobytes()


class ServedEngine:
    """What both engines share: the reference and the HTTP client."""

    info: EngineInfo

    def __init__(self, url: str, transcribe: Callable[[str], str],
                 client: httpx.Client | None = None, timeout: float = 60.0):
        self.url = url
        self.transcribe = transcribe  # recording path -> the words said in it
        self.client = client or httpx.Client(timeout=timeout)

    def builtin_voice(self) -> None:
        return None  # nothing to warm up with

    def prepare_voice(self, wav_path: str) -> Reference:
        text = self.transcribe(wav_path).strip()
        if not text:
            raise ValueError("no words could be made out in the voice recording")
        return Reference(Path(wav_path).read_bytes(), text)

    def speak(self, text: str, voice: Reference, first_in_turn: bool = True) -> Iterator[Piece]:
        response = self.client.post(self.url, json=self.request(text, voice))
        response.raise_for_status()
        yield Piece(to_pcm16(response.content), RATE)

    def request(self, text: str, voice: Reference) -> dict:
        raise NotImplementedError


class MingEngine(ServedEngine):
    info = EngineInfo(name="ming", sample_rate=RATE, min_reference_seconds=3.0)

    @classmethod
    def from_env(cls, transcribe: Callable[[str], str]) -> MingEngine:
        return cls(os.environ.get("MING_URL", "http://127.0.0.1:8095") + "/speak", transcribe)

    def request(self, text: str, voice: Reference) -> dict:
        return {"text": text, "ref_audio": base64.b64encode(voice.wav).decode(),
                "ref_text": voice.text}


class DramaEngine(ServedEngine):
    info = EngineInfo(name="drama", sample_rate=RATE, min_reference_seconds=3.0)

    FIRST_PIECE_SECONDS = 0.2  # the first piece of a sentence goes out this soon
    PIECE_SECONDS = 0.5  # later pieces are about this long
    SILENT_RMS = 0.01  # quieter than this (about -40 dBFS) counts as silence

    def __init__(self, url: str, transcribe: Callable[[str], str],
                 client: httpx.Client | None = None, timeout: float = 60.0,
                 stream: bool = True, seconds_per_word: float = 0.6,
                 stop_silence: float = 1.0):
        super().__init__(url, transcribe, client, timeout)
        self.stream = stream
        self.seconds_per_word = seconds_per_word
        self.stop_silence = stop_silence
        self.warmed = False

    def prepare_voice(self, wav_path: str) -> Reference:
        """As for any served engine, and the first time, one throwaway sentence:
        vLLM-Omni's first request takes about 10 s, and this way it is spent
        before the call says ready, not in the caller's first line."""
        voice = super().prepare_voice(wav_path)
        if not self.warmed:
            try:
                for _ in self.speak("Hello, can you hear me?", voice):
                    pass
            except Exception as e:  # the call itself will show the error
                log.warning("drama: warm-up failed: %s", e)
            self.warmed = True
        return voice

    @classmethod
    def from_env(cls, transcribe: Callable[[str], str]) -> DramaEngine:
        return cls(os.environ.get("DRAMA_URL", "http://127.0.0.1:8091/v1/audio/speech"),
                   transcribe, stream=os.environ.get("DRAMA_STREAM", "1") != "0",
                   seconds_per_word=float(os.environ.get("DRAMA_SECONDS_PER_WORD", "0.6")),
                   stop_silence=float(os.environ.get("DRAMA_STOP_SILENCE", "1.0")))

    def request(self, text: str, voice: Reference) -> dict:
        audio = base64.b64encode(voice.wav).decode()
        body = {"model": "fishaudio/drama-3", "input": text, "voice": "default",
                "ref_audio": f"data:audio/wav;base64,{audio}", "ref_text": voice.text,
                "response_format": "wav"}
        if self.stream:
            body.update(response_format="pcm", stream=True, stream_format="sse")
        return body

    def max_seconds(self, text: str) -> float:
        """The longest a sentence may run: generous for real speech, short of a runaway."""
        return 2.0 + self.seconds_per_word * len(text.split())

    def speak(self, text: str, voice: Reference, first_in_turn: bool = True) -> Iterator[Piece]:
        if not self.stream:
            yield from super().speak(text, voice, first_in_turn)
            return
        with self.client.stream("POST", self.url, json=self.request(text, voice)) as response:
            response.raise_for_status()
            yield from self.pieces(audio_events(response.iter_lines()), text)

    def pieces(self, chunks: Iterator[tuple[bytes, int]], text: str) -> Iterator[Piece]:
        """Streamed PCM chunks in, protocol-rate pieces out, stopped at the end of speech."""
        limit = self.max_seconds(text)
        resampler = None
        ready = np.zeros(0, dtype=np.float32)  # converted audio not sent yet
        held = np.zeros(0, dtype=np.float32)  # silence after speech, sent only if speech resumes
        spoken = 0.0
        heard_speech = False
        first = True
        for pcm, source_rate in chunks:
            if resampler is None:
                resampler = StreamResampler(source_rate)
            audio = resampler.feed(pcm16_to_float(pcm))
            if not len(audio):
                continue
            seconds = len(audio) / RATE
            quiet = float(np.sqrt(np.mean(audio * audio))) < self.SILENT_RMS
            if quiet and heard_speech:
                held = np.concatenate([held, audio])
                if len(held) / RATE >= self.stop_silence:
                    break  # the words are over; the rest would be silence or noise
            else:
                heard_speech = heard_speech or not quiet
                ready = np.concatenate([ready, held, audio])
                held = np.zeros(0, dtype=np.float32)
            spoken += seconds
            if spoken >= limit:
                log.warning("drama: sentence of %d words cut at %.1f s", len(text.split()), spoken)
                break
            want = self.FIRST_PIECE_SECONDS if first else self.PIECE_SECONDS
            if len(ready) / RATE >= want:
                yield Piece(float_to_pcm16(ready), RATE)
                ready = np.zeros(0, dtype=np.float32)
                first = False
        if len(ready):
            yield Piece(float_to_pcm16(ready), RATE)


def audio_events(lines: Iterator[str]) -> Iterator[tuple[bytes, int]]:
    """vLLM-Omni's speech events (server-sent events) as (PCM, sample rate).

    Raises RuntimeError on an error event. Logs a truncated sentence.
    """
    data: list[str] = []
    for line in itertools.chain(lines, [""]):  # a last blank line ends an unterminated event
        if line.startswith("data:"):
            data.append(line[5:].strip())
            continue
        if line.strip() or not data:
            continue  # "event:" lines and comments; the type is in the data too
        payload, data = "\n".join(data), []
        if payload == "[DONE]":
            return
        event = json.loads(payload)
        kind = event.get("type")
        if kind == "speech.audio.delta":
            yield base64.b64decode(event["audio"]), int(event.get("sample_rate") or 44_100)
        elif kind == "speech.audio.done":
            if event.get("truncated"):
                log.warning("drama: the server cut a sentence at its token limit")
            return
        elif kind == "speech.audio.error":
            error = event.get("error") or {}
            raise RuntimeError(f"drama: {error.get('message') or 'the voice engine failed'}")
