"""Voice engines that run as their own server next to the call server.

    TTS_ENGINE=ming    Ming-3.0-tts-0.5B (inclusionAI), behind pod/ming_server.py
    TTS_ENGINE=drama   Fish Audio Drama 3, behind a vLLM-Omni server

Both clone a voice from a recording plus the words said in it, so preparing
a voice transcribes the recording with the call server's own speech to text.
The recording and its words are sent with every request and kept only in
this call's voice handle, as with Chatterbox (parts.TTSEngine). Neither is
logged.

Both make whole sentences, not streamed pieces, so the caller starts
speaking once the first sentence is ready. Audio is converted to 24 kHz,
the rate the call protocol sends.

    MING_URL    default http://127.0.0.1:8095
    DRAMA_URL   default http://127.0.0.1:8091/v1/audio/speech

AI-assisted code (written with Claude Code), cited per the CSC398 AI policy.
"""

from __future__ import annotations

import base64
import io
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

import httpx
import numpy as np

from ..parts import EngineInfo, Piece

RATE = 24_000  # what the call protocol sends (PROTOCOL.md)


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

    @classmethod
    def from_env(cls, transcribe: Callable[[str], str]) -> DramaEngine:
        return cls(os.environ.get("DRAMA_URL", "http://127.0.0.1:8091/v1/audio/speech"),
                   transcribe)

    def request(self, text: str, voice: Reference) -> dict:
        audio = base64.b64encode(voice.wav).decode()
        return {"model": "fishaudio/drama-3", "input": text, "voice": "default",
                "ref_audio": f"data:audio/wav;base64,{audio}", "ref_text": voice.text,
                "response_format": "wav"}
