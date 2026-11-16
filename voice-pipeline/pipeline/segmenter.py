"""Turns a continuous microphone stream into utterances (hands-free calls).

The browser sends 16 kHz mono PCM in small frames. Silero VAD (the
silero-vad package's VADIterator, 512-sample windows = 32 ms) marks where
speech starts and ends. An utterance is emitted when the speaker has been
silent for `end_silence_ms`. Very short blips (coughs, clicks) under
`min_speech_ms` are dropped, and anything longer than `max_utterance_s` is
cut so one person talking forever cannot stall a turn.

Trade-off: end_silence_ms is added to every turn's delay (the system must
hear silence to know you finished). Too short and it cuts people off when
they pause to think.

    HF_END_SILENCE_MS   default 600

AI-assisted code (written with Claude Code), cited per the CSC398 AI policy.
"""

from __future__ import annotations

import os

import numpy as np
import torch
from silero_vad import VADIterator, load_silero_vad

from .parts import Utterance

SR = 16000
WINDOW = 512  # samples per VAD step at 16 kHz

_MODEL = None


def _model():
    global _MODEL
    if _MODEL is None:
        _MODEL = load_silero_vad()
    return _MODEL


def pcm16_to_float(data: bytes) -> np.ndarray:
    return np.frombuffer(data[:len(data) // 2 * 2], dtype="<i2").astype(np.float32) / 32768.0


class VADSegmenter:
    def __init__(self, threshold: float = 0.5, end_silence_ms: int | None = None,
                 min_speech_ms: int = 300, pre_roll_ms: int = 300,
                 max_utterance_s: float = 20.0):
        if end_silence_ms is None:
            end_silence_ms = int(os.environ.get("HF_END_SILENCE_MS", "600"))
        self.vad = VADIterator(_model(), threshold=threshold, sampling_rate=SR,
                               min_silence_duration_ms=end_silence_ms, speech_pad_ms=100)
        self.min_speech = int(SR * min_speech_ms / 1000)
        self.pre_roll = int(SR * pre_roll_ms / 1000)
        self.max_len = int(SR * max_utterance_s)
        self.reset()

    def reset(self) -> None:
        """Forget everything (e.g. while the caller is speaking)."""
        self.vad.reset_states()
        self.audio = np.zeros(0, dtype=np.float32)  # all samples since reset
        self.offset = 0  # absolute sample index of self.audio[0]
        self.pending = np.zeros(0, dtype=np.float32)  # < WINDOW samples not yet fed
        self.start = None  # absolute sample index where speech started

    def feed(self, data: bytes) -> list[Utterance]:
        """Add microphone audio (PCM16); return any finished utterances."""
        pcm = pcm16_to_float(data)
        out = []
        self.audio = np.concatenate([self.audio, pcm])
        self.pending = np.concatenate([self.pending, pcm])
        while len(self.pending) >= WINDOW:
            win, self.pending = self.pending[:WINDOW], self.pending[WINDOW:]
            ev = self.vad(torch.from_numpy(win.copy()))
            if ev and "start" in ev and self.start is None:
                self.start = max(self.offset, int(ev["start"]) - self.pre_roll)
            if ev and "end" in ev and self.start is not None:
                utt = self._take(int(ev["end"]))
                if utt is not None:
                    out.append(utt)
            if self.start is not None:
                end_now = self.offset + len(self.audio) - len(self.pending)
                if end_now - self.start >= self.max_len:  # force a cut
                    utt = self._take(end_now)
                    self.vad.reset_states()
                    if utt is not None:
                        out.append(utt)
        # Keep memory bounded when nobody is talking: hold only the pre-roll.
        if self.start is None and len(self.audio) > self.pre_roll + WINDOW:
            drop = len(self.audio) - self.pre_roll
            self.audio, self.offset = self.audio[drop:], self.offset + drop
        return out

    def _take(self, end_abs: int) -> Utterance | None:
        s, e = self.start - self.offset, end_abs - self.offset
        self.start = None
        utt = self.audio[max(0, s):max(0, e)]
        if len(utt) < self.min_speech:
            return None
        return Utterance(audio=utt.copy(), seconds=len(utt) / SR)
