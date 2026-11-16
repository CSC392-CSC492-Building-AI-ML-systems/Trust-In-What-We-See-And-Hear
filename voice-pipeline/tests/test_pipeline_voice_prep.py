"""Tests for tidying a voice recording. Needs numpy, which the backend's
environment does not have, so these are skipped there.

AI-assisted code (written with Claude Code), cited per the CSC398 AI policy.
"""

import sys
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")

sys.path.insert(0, str(Path(__file__).parents[1]))
from pipeline.tts.voice_prep import tidy  # noqa: E402

RATE = 24000


def tone(seconds: float, level: float = 0.05) -> np.ndarray:
    t = np.arange(int(RATE * seconds)) / RATE
    return (level * np.sin(2 * np.pi * 180 * t)).astype(np.float32)


def hush(seconds: float) -> np.ndarray:
    return (np.random.default_rng(0).normal(0, 1e-4, int(RATE * seconds))).astype(np.float32)


def rms_db(x: np.ndarray) -> float:
    return 20 * np.log10(np.sqrt(np.mean(x ** 2)))


def test_long_pauses_are_shortened_and_the_ends_are_trimmed():
    recording = np.concatenate([hush(1.0), tone(2.0), hush(2.0), tone(2.0), hush(1.5)])
    out = tidy(recording, RATE)
    # 4 s of speech, one pause cut to about a quarter second, plus padding.
    assert 4.0 <= len(out) / RATE <= 5.0
    assert abs(out[:RATE // 5]).max() > 0.01  # speech starts within 0.2 s


def test_a_quiet_recording_is_raised_to_a_speaking_level_without_clipping():
    out = tidy(np.concatenate([tone(3.0, level=0.01), hush(0.5), tone(3.0, level=0.01)]), RATE)
    assert rms_db(out) == pytest.approx(-20, abs=2)
    assert abs(out).max() <= 10 ** (-1 / 20) + 1e-6


def test_short_pauses_inside_speech_are_left_alone():
    recording = np.concatenate([tone(2.0), hush(0.2), tone(2.0)])
    assert len(tidy(recording, RATE)) / RATE == pytest.approx(4.2, abs=0.1)


def test_a_recording_with_no_clear_speech_keeps_its_length():
    steady = tone(3.0)
    assert len(tidy(steady, RATE)) == len(steady)
    assert len(tidy(np.zeros(RATE, dtype=np.float32), RATE)) == RATE
