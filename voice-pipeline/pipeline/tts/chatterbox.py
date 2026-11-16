"""Chatterbox as a text-to-speech engine (parts.TTSEngine).

Two variants: the original model ("chatterbox") and Chatterbox Turbo
("turbo"), which is faster and can perform sound tags like [sigh].

A voice is the model's "conditionals" computed from a recording. The model
keeps one set at a time in `model.conds`, so the handle returned by
prepare_voice is that object, and speak() puts it back before generating.
The caller (models.Models) makes sure only one call speaks at a time.

How much of a recording each variant uses (from the library source): the
original takes the first 6 s for the speech prompt and 10 s for the decoder;
Turbo takes 15 s and 10 s and refuses recordings of 5 s or less. Longer
recordings do not hurt, but only the speaker embedding uses all of it.

    TTS_ENGINE            chatterbox (default) or turbo
    CB_EXAGGERATION       original only, default 0.7 (0.8 sounded best in
                          listening tests but glitched)
    CB_CFG                original only, default 0.35
    TTS_STREAM            1 (default) sends audio in pieces as it is made
    STREAM_FIRST_TOKENS   12, about 0.5 s of audio in the first piece
    STREAM_SECOND_TOKENS  20
    STREAM_CHUNK_TOKENS   40, about 1.6 s per later piece
    STREAM_CONTEXT_TOKENS 12, earlier tokens re-decoded per piece; more made
                          each piece slower than it plays
    STREAM_CFM_STEPS      5 decoder steps (0 = the library's default)

AI-assisted code (written with Claude Code), cited per the CSC398 AI policy.
"""

from __future__ import annotations

import os
from typing import Any, Iterator

import numpy as np

from ..parts import EngineInfo, Piece

TURBO_TAGS = ("[sigh]", "[sniff]", "[gasp]", "[groan]", "[clear throat]")


def to_pcm16(audio: np.ndarray) -> bytes:
    return (np.clip(audio, -1.0, 1.0) * 32767).astype("<i2").tobytes()


class ChatterboxEngine:
    def __init__(self, variant: str = "chatterbox", device: str = "cuda",
                 exaggeration: float = 0.7, cfg_weight: float = 0.35, stream: bool = True,
                 first_tokens: int = 12, second_tokens: int = 20, chunk_tokens: int = 40,
                 context_tokens: int = 12, cfm_steps: int | None = 5):
        if variant not in ("chatterbox", "turbo"):
            raise ValueError(f"unknown Chatterbox variant: {variant}")
        self.turbo = variant == "turbo"
        if self.turbo:
            from chatterbox.tts_turbo import ChatterboxTurboTTS
            self.m = ChatterboxTurboTTS.from_pretrained(device=device)
        else:
            from chatterbox.tts import ChatterboxTTS
            self.m = ChatterboxTTS.from_pretrained(device=device)
        self.info = EngineInfo(
            name=variant, sample_rate=self.m.sr,
            min_reference_seconds=5.1 if self.turbo else 5.0,
            sound_tags=TURBO_TAGS if self.turbo else ())
        self.exaggeration = exaggeration
        self.cfg_weight = cfg_weight
        self.stream = stream
        self.sizes = dict(first=first_tokens, second=second_tokens, chunk=chunk_tokens,
                          context=context_tokens, cfm_steps=cfm_steps)
        self.streamer = None

    @classmethod
    def from_env(cls) -> ChatterboxEngine:
        env = os.environ.get
        return cls(
            env("TTS_ENGINE", "chatterbox"),
            exaggeration=float(env("CB_EXAGGERATION", "0.7")),
            cfg_weight=float(env("CB_CFG", "0.35")),
            stream=env("TTS_STREAM", "1") == "1",
            first_tokens=int(env("STREAM_FIRST_TOKENS", "12")),
            second_tokens=int(env("STREAM_SECOND_TOKENS", "20")),
            chunk_tokens=int(env("STREAM_CHUNK_TOKENS", "40")),
            context_tokens=int(env("STREAM_CONTEXT_TOKENS", "12")),
            cfm_steps=int(env("STREAM_CFM_STEPS", "5")) or None,
        )

    def builtin_voice(self) -> Any:
        """The model's own default voice, if it ships one. Used to warm up."""
        return getattr(self.m, "conds", None)

    def prepare_voice(self, wav_path: str) -> Any:
        if self.turbo:
            self.m.prepare_conditionals(wav_path)
        else:
            self.m.prepare_conditionals(wav_path, exaggeration=self.exaggeration)
        return self.m.conds

    def speak(self, text: str, voice: Any, first_in_turn: bool = True) -> Iterator[Piece]:
        self.m.conds = voice
        tuning = {} if self.turbo else dict(exaggeration=self.exaggeration,
                                            cfg_weight=self.cfg_weight)
        if not self.stream:
            wav = self.m.generate(text, **tuning)
            yield Piece(to_pcm16(wav.squeeze(0).float().cpu().numpy()), self.info.sample_rate)
            return
        if self.streamer is None:
            from .chatterbox_streaming import StreamingChatterbox
            self.streamer = StreamingChatterbox(self.m, self.turbo)
        s = self.sizes
        # Only the first text chunk of a reply starts with a tiny piece. Later
        # chunks already have audio queued ahead of them, so they use
        # full-size pieces (less overhead).
        pieces = self.streamer.stream(
            text, first_tokens=s["first"] if first_in_turn else s["chunk"],
            second_tokens=s["second"] if first_in_turn else None,
            chunk_tokens=s["chunk"], context_tokens=s["context"],
            cfm_steps=s["cfm_steps"], **tuning)
        for audio, _info in pieces:
            yield Piece(to_pcm16(audio), self.info.sample_rate)
