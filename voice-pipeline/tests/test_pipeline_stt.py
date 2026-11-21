"""Tests for the speech-to-text confidence check, with Whisper replaced.

AI-assisted code (written with Claude Code), cited per the CSC398 AI policy.
"""

import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
if "faster_whisper" not in sys.modules:
    try:
        import faster_whisper  # noqa: F401
    except ImportError:  # the backend environment has no audio libraries
        sys.modules["faster_whisper"] = types.SimpleNamespace(WhisperModel=None)
from pipeline.stt import RobustSTT  # noqa: E402


class FakeWhisper:
    def __init__(self, text: str, avg_logprob: float, no_speech_prob: float, speech_s: float):
        self.segment = SimpleNamespace(text=text, start=0.0, end=speech_s,
                                       avg_logprob=avg_logprob, no_speech_prob=no_speech_prob)
        self.info = SimpleNamespace(duration_after_vad=speech_s)

    def transcribe(self, audio, **kwargs):
        return iter([self.segment]), self.info


def heard(text, avg_logprob, no_speech_prob, speech_s):
    stt = RobustSTT.__new__(RobustSTT)
    stt.__dict__.update(beam_size=5, vad=True, vad_parameters={}, min_speech_s=0.25,
                        max_no_speech_prob=0.6, min_avg_logprob=-1.0)
    stt.model = FakeWhisper(text, avg_logprob, no_speech_prob, speech_s)
    return stt.transcribe([0.0] * int(16000 * max(speech_s, 1.0)))  # only its length is read


# Recorded speech from the review of #14 (large-v3-turbo on an RTX 3060 Ti):
# every one was heard correctly and used to be rejected.
@pytest.mark.parametrize("text, avg_logprob, speech_s", [
    ("Okay.", -0.73, 0.74),
    ("Okay", -0.61, 0.67),
    ("Okay", -0.90, 1.80),
    ("Bye.", -0.46, 0.68),
    ("Thank you.", -0.37, 0.83),
    ("I'm sorry.", -0.47, 0.96),
])
def test_real_short_answers_are_accepted(text, avg_logprob, speech_s):
    result = heard(text, avg_logprob, 0.0, speech_s)
    assert result.accepted, result.reason


@pytest.mark.parametrize("text, avg_logprob, no_speech_prob", [
    ("Okay.", -0.4, 0.5),  # Whisper doubts there was speech
    ("Thank you.", -1.2, 0.0),  # and unsure of the words
    ("Thanks for watching!", -0.7, 0.0),  # never said on a call
    ("Beautiful.", -0.3, 0.0),  # too short to trust (0.8 s)
])
def test_likely_hallucinations_are_rejected(text, avg_logprob, no_speech_prob):
    result = heard(text, avg_logprob, no_speech_prob, 0.8)
    assert not result.accepted
    assert result.reason == "likely hallucination"


def test_other_text_is_not_checked_against_the_list():
    assert heard("Okay, I'll send it.", -0.44, 0.0, 1.36).accepted
