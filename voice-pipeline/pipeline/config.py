"""Call settings, read from environment variables.

Model settings live next to each model (stt.py, llm.py, tts/chatterbox.py).

AI-assisted code (written with Claude Code), cited per the CSC398 AI policy.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    # Longest call the server allows, whatever the scenario asks for.
    max_call_seconds: float = 600.0
    # Calls at once. The GPU does one job at a time, so more means slower replies.
    max_calls: int = 2
    # If the client never sends `played`, listen again this long after the
    # turn's audio would have finished.
    played_grace_seconds: float = 1.0
    # Messages of the conversation the LLM sees (the rest stays in memory).
    history_messages: int = 20
    # Turns that may fail in a row before the call ends with reason "error".
    max_failed_turns: int = 2

    @classmethod
    def from_env(cls) -> Settings:
        env = os.environ.get
        return cls(
            max_call_seconds=float(env("PIPELINE_MAX_CALL_SECONDS", "600")),
            max_calls=int(env("PIPELINE_MAX_CALLS", "2")),
        )
