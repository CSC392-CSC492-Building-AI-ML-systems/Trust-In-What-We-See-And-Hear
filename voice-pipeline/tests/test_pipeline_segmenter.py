"""Tests for the segmenter's bookkeeping, with a fake speech detector.

segmenter.py imports torch and silero-vad, which the backend's environment
does not have, so these tests are skipped there. They run wherever the
pipeline's own requirements are installed.

AI-assisted code (written with Claude Code), cited per the CSC398 AI policy.
"""

import sys
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("torch")
pytest.importorskip("silero_vad")

sys.path.insert(0, str(Path(__file__).parents[1]))
from pipeline.segmenter import SR, WINDOW, VADSegmenter  # noqa: E402

LOUD = (np.full(SR, 8000, dtype="<i2")).tobytes()  # one second that counts as speech
QUIET = bytes(2 * SR)


class LoudnessVAD:
    """Counts samples and reports events the way silero's VADIterator does."""

    def __init__(self, end_after_windows: int = 10):
        self.end_after = end_after_windows
        self.reset_states()

    def reset_states(self):
        self.current_sample = 0
        self.triggered = False
        self.quiet = 0

    def __call__(self, window):
        self.current_sample += WINDOW
        if float(window.abs().max()) > 0.1:
            self.quiet = 0
            if not self.triggered:
                self.triggered = True
                return {"start": self.current_sample - WINDOW}
        elif self.triggered:
            self.quiet += 1
            if self.quiet >= self.end_after:
                self.triggered = False
                return {"end": self.current_sample - self.quiet * WINDOW}
        return None


def segmenter(**settings) -> VADSegmenter:
    seg = VADSegmenter(pre_roll_ms=0, **settings)
    seg.vad = LoudnessVAD()
    seg.reset()
    return seg


def test_speech_then_silence_is_one_utterance():
    seg = segmenter()
    assert seg.feed(LOUD + LOUD) == []
    (utterance,) = seg.feed(QUIET)
    assert utterance.seconds == pytest.approx(2.0, abs=0.1)


def test_speech_that_goes_on_after_a_forced_cut_is_not_lost():
    seg = segmenter(max_utterance_s=3.0)
    cut = [u for _ in range(5) for u in seg.feed(LOUD)]  # 5 s of talking, no pause
    assert [round(u.seconds) for u in cut] == [3]
    rest = seg.feed(QUIET)
    assert [round(u.seconds) for u in rest] == [2]
