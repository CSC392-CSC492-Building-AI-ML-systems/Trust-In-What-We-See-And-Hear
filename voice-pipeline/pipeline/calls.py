"""Where a call's setup comes from: who may connect, and with what.

A client connects with a call ID and a token. The pipeline turns those into
a CallSetup: the call context, where the persona's voice recording is, and
the time limit.

When the call is over, the pipeline hands back a CallReport: how it ended and
its score.

Two sources, with the same `claim` and `report` methods:

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

import asyncio
import hmac
import json
import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import httpx

SAMPLE_CONTEXT = (Path(__file__).resolve().parents[2]
                  / "backend/app/modules/calls/sample_call_context.json")
MAX_VOICE_BYTES = 25 * 1024 * 1024
log = logging.getLogger("pipeline.calls")


@dataclass
class CallSetup:
    context: dict  # untrusted; validated with CallContext.from_dict
    voice: str  # a local path, or an https link to the recording
    max_duration_seconds: float


@dataclass
class CallReport:
    """What the pipeline says about a call once it is over."""

    ended_by: str  # the call_ended reason
    duration_seconds: int
    result: dict | None  # the score and summary (scoring.py), or None if there is none

    def body(self) -> dict:
        return {"ended_by": self.ended_by, "duration_seconds": self.duration_seconds,
                "result": self.result}


class DevCalls:
    KEPT_REPORTS = 50
    report_budget_seconds = 0.0  # the longest report() can take: it only stores

    def __init__(self, token: str, voice: str, context_path: str | Path = SAMPLE_CONTEXT,
                 max_duration_seconds: float = 300.0):
        self.token = token
        self.voice = voice
        self.context_path = Path(context_path)
        self.max_duration_seconds = max_duration_seconds
        # call ID -> its report, or None while the call is going on. In memory
        # only, for looking at results without a backend (see session.py).
        self.reports: dict[str, CallReport | None] = {}

    def accepts(self, token: str) -> bool:
        return bool(self.token) and hmac.compare_digest(token.encode(), self.token.encode())

    def _keep(self, call_id: str, report: CallReport | None) -> None:
        self.reports.pop(call_id, None)
        self.reports[call_id] = report
        while len(self.reports) > self.KEPT_REPORTS:
            del self.reports[next(iter(self.reports))]  # oldest first

    async def report(self, call_id: str, report: CallReport) -> None:
        self._keep(call_id, report)

    @classmethod
    def from_env(cls) -> DevCalls:
        env = os.environ.get
        return cls(env("PIPELINE_DEV_TOKEN", ""), env("PIPELINE_DEV_VOICE", ""),
                   env("PIPELINE_DEV_CONTEXT") or SAMPLE_CONTEXT,
                   float(env("PIPELINE_DEV_SECONDS", "300")))

    async def claim(self, call_id: str, token: str) -> CallSetup | None:
        """The setup for this call, or None if the token is not accepted."""
        if not self.accepts(token):
            return None
        try:
            context = json.loads(self.context_path.read_text())
        except (OSError, ValueError) as e:
            # Before the call is marked as going on, so it is not left that way.
            raise CallsUnavailable("the dev context file could not be read") from e
        self._keep(call_id, None)
        return CallSetup(context, self.voice, self.max_duration_seconds)


class CallsUnavailable(Exception):
    """The backend could not be asked, or its answer made no sense."""


class BackendCalls:
    """Asks the backend to check the token and hand over the call's setup."""

    TIMEOUT = 10.0  # seconds, per request

    def __init__(self, url: str, secret: str, client: httpx.AsyncClient | None = None,
                 report_retry_seconds: tuple[float, ...] = (1.0, 4.0)):
        self.url = url.rstrip("/")
        self.secret = secret
        # One client for the server's life, so calls reuse the connection.
        self.client = client or httpx.AsyncClient(timeout=self.TIMEOUT)
        self.report_retry_seconds = report_retry_seconds
        # The longest report() can take: every attempt timing out.
        self.report_budget_seconds = (
            (len(report_retry_seconds) + 1) * self.TIMEOUT + sum(report_retry_seconds))

    async def aclose(self) -> None:
        await self.client.aclose()

    @classmethod
    def from_env(cls) -> BackendCalls:
        return cls(os.environ["PIPELINE_BACKEND_URL"],
                   os.environ.get("PIPELINE_BACKEND_SECRET", ""))

    async def claim(self, call_id: str, token: str) -> CallSetup | None:
        """The setup for this call, or None if the backend refuses the token.

        The backend accepts a token once, so a call is claimed only when it
        can really start.
        """
        try:
            response = await self.client.post(
                f"{self.url}/api/v1/internal/calls/{quote(call_id, safe='')}/claim",
                json={"token": token},
                headers={"Authorization": f"Bearer {self.secret}"})
        except httpx.HTTPError as e:
            raise CallsUnavailable("the backend could not be reached") from e
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

    async def report(self, call_id: str, report: CallReport) -> None:
        """Hand the call's result to the backend. Tries a few times, then gives up.

        The result exists nowhere else, so a backend that stays down loses it.
        """
        url = f"{self.url}/api/v1/internal/calls/{quote(call_id, safe='')}/result"
        for delay in (*self.report_retry_seconds, None):
            try:
                response = await self.client.post(
                    url, json=report.body(),
                    headers={"Authorization": f"Bearer {self.secret}"})
                if response.status_code < 300:
                    return
                problem = f"the backend answered {response.status_code}"
                if response.status_code < 500 and response.status_code != 429:
                    break  # it understood and refused; asking again will not help
            except httpx.HTTPError:
                problem = "the backend could not be reached"
            if delay is not None:
                await asyncio.sleep(delay)
        log.error("call %s: result not delivered, %s", call_id, problem)


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
    """How long a WAV file plays, from its header.

    Not the `wave` module: on Python 3.11 it refuses formats the voice engine
    reads fine (extensible and float WAVs, which audio tools often write).
    """
    try:
        with open(path, "rb") as f:
            size = os.fstat(f.fileno()).st_size
            start = f.read(12)
            byte_rate = 0
            while start[:4] == b"RIFF" and start[8:] == b"WAVE":
                head = f.read(8)
                if len(head) < 8:
                    break
                kind, length = head[:4], int.from_bytes(head[4:], "little")
                if kind == b"fmt ":
                    byte_rate = int.from_bytes(f.read(length)[8:12], "little")
                    f.seek(length % 2, 1)
                elif kind == b"data":
                    if byte_rate <= 0:
                        break
                    # A file written as a stream has no true length here.
                    left = size - f.tell()
                    return (length if 0 < length <= left else left) / byte_rate
                else:
                    f.seek(length + length % 2, 1)  # chunks are padded to even sizes
    except OSError as e:
        raise VoiceError("voice recording is not a readable WAV file") from e
    raise VoiceError("voice recording is not a readable WAV file")
