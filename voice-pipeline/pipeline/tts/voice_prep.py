"""Tidies a voice recording before the voice engine reads it.

Chatterbox builds most of a voice from the first 6 to 10 seconds of the
recording. A real recording starts late, has pauses and is often quiet: in a
test, the first 6 seconds of a 23 second recording were less than half
speech, and the clone sounded little like the speaker. So before cloning:

- long pauses are shortened, which puts more speech in those first seconds
- the level is raised to a normal speaking level

Only numpy is needed, so this can be tested without the voice engine.

    VOICE_PREP   1 (default) tidies recordings, 0 uses them as they are

AI-assisted code (written with Claude Code), cited per the CSC398 AI policy.
"""

from __future__ import annotations

import numpy as np

FRAME_S = 0.02  # loudness is measured in steps this long
KEEP_PAUSE_S = 0.25  # how much of each pause stays, so speech still sounds natural
PAD_S = 0.1  # kept around each stretch of speech, so word edges are not cut
TARGET_DB = -20.0  # speech level after tidying (RMS, dBFS)
PEAK_DB = -1.0  # never louder than this at any sample


def speech_frames(audio: np.ndarray, rate: int) -> np.ndarray:
    """One boolean per frame: True where someone is speaking."""
    size = int(rate * FRAME_S)
    count = len(audio) // size
    if count == 0:
        return np.zeros(0, dtype=bool)
    frames = audio[:count * size].reshape(count, size)
    db = 20 * np.log10(np.sqrt(np.mean(frames ** 2, axis=1)) + 1e-9)
    quiet, loud = np.percentile(db, 10), np.percentile(db, 90)
    if loud - quiet < 12:  # no clear gap between speech and background
        return np.ones(count, dtype=bool)
    speaking = db > (quiet + loud) / 2
    pad = int(round(PAD_S / FRAME_S))
    padded = speaking.copy()
    for shift in range(1, pad + 1):
        padded[shift:] |= speaking[:-shift]
        padded[:-shift] |= speaking[shift:]
    return padded


def tidy(audio: np.ndarray, rate: int) -> np.ndarray:
    """The recording with long pauses shortened and a normal speaking level.

    audio: mono float samples in -1..1. Returns the same kind of array. A
    recording with no clear speech is returned with only its level changed.
    """
    audio = np.asarray(audio, dtype=np.float32)
    speaking = speech_frames(audio, rate)
    size = int(rate * FRAME_S)
    keep = np.zeros(len(speaking), dtype=bool)
    allowed = int(round(KEEP_PAUSE_S / FRAME_S))
    pause = allowed  # so silence before the first word is dropped entirely
    for i, is_speech in enumerate(speaking):
        if is_speech:
            pause = 0
            keep[i] = True
        else:
            pause += 1
            keep[i] = pause <= allowed
    if keep.any():
        last = len(keep) - int(np.argmax(speaking[::-1])) if speaking.any() else len(keep)
        keep[last:] = False  # and silence after the last word
        parts = [audio[i * size:(i + 1) * size] for i in np.flatnonzero(keep)]
        tidied = np.concatenate(parts) if parts else audio
    else:
        tidied = audio
    if len(tidied) == 0:
        tidied = audio
    voiced = tidied[np.abs(tidied) > 10 ** (-50 / 20)]
    level = np.sqrt(np.mean(voiced ** 2)) if len(voiced) else 0.0
    peak = float(np.abs(tidied).max()) if len(tidied) else 0.0
    if level > 0 and peak > 0:
        gain = min(10 ** (TARGET_DB / 20) / level, 10 ** (PEAK_DB / 20) / peak)
        tidied = tidied * gain
    return tidied.astype(np.float32)
