"""Small data types passed between the call session and the models.

Nothing here imports a model library, so the session and its tests run
without a GPU.

AI-assisted code (written with Claude Code), cited per the CSC398 AI policy.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterator, Protocol


@dataclass
class Utterance:
    """One stretch of the user's speech, cut from the microphone stream."""
    audio: Any  # whatever the speech-to-text model takes
    seconds: float


@dataclass
class Heard:
    """What speech to text made of an utterance."""
    text: str
    accepted: bool  # False for noise or a likely made-up phrase
    reason: str = ""


@dataclass
class Piece:
    """A bit of the caller's speech: mono signed 16-bit little-endian PCM."""
    pcm16: bytes
    sample_rate: int

    @property
    def seconds(self) -> float:
        return len(self.pcm16) / 2 / self.sample_rate


@dataclass(frozen=True)
class EngineInfo:
    """Facts a text-to-speech engine declares about itself."""
    name: str
    sample_rate: int
    min_reference_seconds: float  # shortest voice recording it accepts
    sound_tags: tuple[str, ...] = ()  # tags like [sigh] it can voice, if any


class TTSEngine(Protocol):
    """What the pipeline needs from a text-to-speech engine.

    To add an engine, write a class with these members (see
    tts/chatterbox.py) and pick it in server.py. Nothing else changes.
    """

    info: EngineInfo

    def prepare_voice(self, wav_path: str) -> Any:
        """Read a voice recording and return a handle for speak().

        The handle belongs to one call. The engine must not keep it.
        """

    def speak(self, text: str, voice: Any, first_in_turn: bool) -> Iterator[Piece]:
        """Say the text in that voice, yielding audio as it is made."""
