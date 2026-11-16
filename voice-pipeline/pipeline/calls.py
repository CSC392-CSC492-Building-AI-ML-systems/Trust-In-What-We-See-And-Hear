"""Where a call's setup comes from: who may connect, and with what.

A client connects with a call ID and a token. The pipeline turns those into
a CallSetup: the call context, where the persona's voice recording is, and
the time limit.

Two sources, with the same `claim` method:

BackendCalls asks the backend, as described in BACKEND.md. Used when
PIPELINE_BACKEND_URL is set.

    PIPELINE_BACKEND_URL      the backend's address, e.g. https://api.example
    PIPELINE_BACKEND_SECRET   shared secret the backend expects from us

DevCalls needs no backend: one shared token, one context file and one voice
recording.

    PIPELINE_DEV_TOKEN     required; without it every connection is refused
    PIPELINE_DEV_VOICE     path to a WAV of the voice to clone (required)
    PIPELINE_DEV_CONTEXT   path to a call context JSON file, default the
                           backend's sample_call_context.json
    PIPELINE_DEV_SECONDS   call time limit, default 300

AI-assisted code (written with Claude Code), cited per the CSC398 AI policy.
"""

from __future__ import annotations

import hmac
import json
import os
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import httpx

SAMPLE_CONTEXT = (Path(__file__).resolve().parents[2]
                  / "backend/app/modules/calls/sample_call_context.json")
MAX_VOICE_BYTES = 25 * 1024 * 1024


@dataclass
class CallSetup:
    context: dict  # untrusted; validated with CallContext.from_dict
    voice: str  # a local path, or an https link to the recording
    max_duration_seconds: float


class DevCalls:
    def __init__(self, token: str, voice: str, context_path: str | Path = SAMPLE_CONTEXT,
                 max_duration_seconds: float = 300.0):
        self.token = token
        self.voice = voice
        self.context_path = Path(context_path)
        self.max_duration_seconds = max_duration_seconds

    @classmethod
    def from_env(cls) -> DevCalls:
        env = os.environ.get
        return cls(env("PIPELINE_DEV_TOKEN", ""), env("PIPELINE_DEV_VOICE", ""),
                   env("PIPELINE_DEV_CONTEXT") or SAMPLE_CONTEXT,
                   float(env("PIPELINE_DEV_SECONDS", "300")))

    async def claim(self, call_id: str, token: str) -> CallSetup | None:
        """The setup for this call, or None if the token is not accepted."""
        if not self.token or not hmac.compare_digest(token.encode(), self.token.encode()):
            return None
        return CallSetup(json.loads(self.context_path.read_text()), self.voice,
                         self.max_duration_seconds)


class CallsUnavailable(Exception):
    """The backend could not be asked, or its answer made no sense."""


class BackendCalls:
    """Asks the backend to check the token and hand over the call's setup."""

    def __init__(self, url: str, secret: str, client: httpx.AsyncClient | None = None):
        self.url = url.rstrip("/")
        self.secret = secret
        self.client = client

    @classmethod
    def from_env(cls) -> BackendCalls:
        return cls(os.environ["PIPELINE_BACKEND_URL"],
                   os.environ.get("PIPELINE_BACKEND_SECRET", ""))

    async def claim(self, call_id: str, token: str) -> CallSetup | None:
        """The setup for this call, or None if the backend refuses the token.

        The backend accepts a token once, so a call is claimed only when it
        can really start.
        """
        client = self.client or httpx.AsyncClient(timeout=10)
        try:
            response = await client.post(
                f"{self.url}/api/v1/internal/calls/{quote(call_id, safe='')}/claim",
                json={"token": token},
                headers={"Authorization": f"Bearer {self.secret}"})
        except httpx.HTTPError as e:
            raise CallsUnavailable("the backend could not be reached") from e
        finally:
            if self.client is None:
                await client.aclose()
        if response.status_code in (403, 404):
            return None
        if response.status_code != 200:
            raise CallsUnavailable(f"the backend answered {response.status_code}")
        try:
            body = response.json()
            return CallSetup(body["context"], str(body["voice_url"]),
                             float(body["max_duration_seconds"]))
        except (ValueError, TypeError, KeyError) as e:
            raise CallsUnavailable("the backend's answer was not understood") from e


class VoiceError(Exception):
    """The voice recording could not be fetched or is not usable."""


async def fetch_voice(source: str, client: httpx.AsyncClient | None = None) -> tuple[Path, bool]:
    """Get the recording as a local file. Returns (path, delete it afterwards)."""
    if not source.startswith(("http://", "https://")):
        path = Path(source)
        if not source or not path.is_file():
            raise VoiceError("voice recording not found")
        return path, False
    if not source.startswith("https://"):
        raise VoiceError("voice link must be https")
    own_client = client is None
    client = client or httpx.AsyncClient(timeout=20)
    fd, name = tempfile.mkstemp(suffix=".wav")
    try:
        size = 0
        with os.fdopen(fd, "wb") as out:
            async with client.stream("GET", source) as response:
                response.raise_for_status()
                async for block in response.aiter_bytes():
                    size += len(block)
                    if size > MAX_VOICE_BYTES:
                        raise VoiceError("voice recording is too large")
                    out.write(block)
    except BaseException as e:
        os.unlink(name)
        if isinstance(e, httpx.HTTPError):
            raise VoiceError("voice recording could not be downloaded") from e
        raise
    finally:
        if own_client:
            await client.aclose()
    return Path(name), True


def wav_seconds(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as w:
            return w.getnframes() / w.getframerate()
    except (wave.Error, EOFError, ZeroDivisionError) as e:
        raise VoiceError("voice recording is not a readable WAV file") from e
