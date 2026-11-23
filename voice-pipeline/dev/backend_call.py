"""Make one test call through the backend, the way the app will.

Signs in as a user, starts a call with `POST /api/v1/calls`, connects to the
`ws_url` it returns, answers, plays recorded replies as the user's
microphone, hangs up, and waits for the backend to store the result. It
stands in for the frontend until the app can make calls. Run it from
`backend/`, which has the packages it needs:

    uv run python ../voice-pipeline/dev/backend_call.py \\
        --email alice@example.com --password demo-password reply1.wav reply2.wav

Each reply is a WAV, 16 kHz mono 16-bit; one is sent after each of the
caller's turns. On macOS, make one with:
    say -o reply.wav --data-format=LEI16@16000 "Who is this? Why are you calling?"

The user needs an accepted consent from a friend whose voice is ready.
`--save` keeps the caller's audio for listening to afterwards.

AI-assisted code (written with Claude Code), cited per the CSC398 AI policy.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import sys
import time
import wave
from pathlib import Path

import httpx
import websockets

FRAME_BYTES = 1280  # 40 ms of 16 kHz 16-bit mono
FRAME_SECONDS = 0.04


class CallError(Exception):
    pass


def read_reply(path: Path) -> bytes:
    """A reply's raw PCM. It must already be in the microphone format."""
    with wave.open(str(path), "rb") as w:
        if (w.getframerate(), w.getnchannels(), w.getsampwidth()) != (16000, 1, 2):
            raise CallError(f"{path} must be 16 kHz mono 16-bit (see --help for how to make one)")
        return w.readframes(w.getnframes())


def sign_in(supabase: str, key: str, email: str, password: str) -> str:
    response = httpx.post(f"{supabase}/auth/v1/token?grant_type=password",
                          headers={"apikey": key}, json={"email": email, "password": password})
    if response.status_code != 200:
        raise CallError(f"sign-in failed ({response.status_code}): {response.text[:200]}")
    return response.json()["access_token"]


def pick_consent(items: list[dict], wanted: str | None) -> dict:
    """The consent to call with: the one asked for, else the first usable one."""
    usable = [c for c in items if c.get("direction") == "outgoing" and c.get("status") == "accepted"]
    if wanted:
        usable = [c for c in usable if c["id"] == wanted]
    if not usable:
        raise CallError("no accepted consent from a friend to call with"
                        + (f" with ID {wanted}" if wanted else ""))
    return usable[0]


def start_call(api: httpx.Client, consent: str | None, scenario: str | None) -> dict:
    found = api.get("/api/v1/consents")
    found.raise_for_status()
    chosen = pick_consent(found.json()["items"], consent)
    if not scenario:
        scenarios = api.get("/api/v1/scenarios")
        scenarios.raise_for_status()
        if not scenarios.json()["items"]:
            raise CallError("there are no scenarios")
        scenario = scenarios.json()["items"][0]["id"]
    response = api.post("/api/v1/calls", json={"consent_id": chosen["id"], "scenario_id": scenario})
    if response.status_code >= 300:
        raise CallError(f"POST /calls answered {response.status_code}: {response.text[:300]}")
    return response.json()


async def talk(ws_url: str, replies: list[bytes], save: Path | None, wait_ready: float) -> str:
    """Run the call over the WebSocket. Returns the reason it ended."""
    async with websockets.connect(ws_url, max_size=None) as ws:
        sent = 0
        turn_audio: list[bytes] = []
        turns = 0

        async def next_message(timeout: float) -> dict:
            raw = await asyncio.wait_for(ws.recv(), timeout)
            return json.loads(raw)

        message = await next_message(wait_ready)
        if message.get("type") != "ready":
            raise CallError(f"expected ready, got {message}")
        print(f"ready: the caller's voice is set up; calls are capped at "
              f"{message.get('max_duration_seconds')} s")
        await ws.send(json.dumps({"type": "answered"}))
        while True:
            message = await next_message(120)
            kind = message.get("type")
            if kind == "audio":
                turn_audio.append(base64.b64decode(message["audio"]))
                if message.get("text"):
                    print(f"  caller: {message['text']}")
            elif kind == "transcript":
                print(f"  heard:  {message.get('text')!r}")
            elif kind == "error":
                print(f"  error:  {message.get('code')}: {message.get('message')}")
            elif kind == "turn_done":
                turns += 1
                if save and turn_audio:
                    save.mkdir(parents=True, exist_ok=True)
                    for i, piece in enumerate(turn_audio):
                        (save / f"turn{turns:02d}_{i:02d}.wav").write_bytes(piece)
                turn_audio = []
                await ws.send(json.dumps({"type": "played", "turn_id": message["turn_id"]}))
                if sent == len(replies):
                    print("no replies left: hanging up")
                    await ws.send(json.dumps({"type": "hangup"}))
                    continue
                print(f"  user:   (reply {sent + 1}, {len(replies[sent]) / 32000:.1f} s)")
                # Real time, then silence so the server hears the end of speech.
                for pcm in (replies[sent], bytes(32000 * 2)):
                    for start in range(0, len(pcm), FRAME_BYTES):
                        await ws.send(pcm[start:start + FRAME_BYTES])
                        await asyncio.sleep(FRAME_SECONDS)
                sent += 1
            elif kind == "call_ended":
                return str(message.get("reason"))


def wait_result(api: httpx.Client, call_id: str, timeout: float = 90) -> dict:
    deadline = time.time() + timeout
    while True:
        call = api.get(f"/api/v1/calls/{call_id}").json()
        if call.get("status") in ("completed", "failed") or time.time() > deadline:
            return call
        time.sleep(3)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("replies", nargs="*", type=Path, help="the user's replies, in order")
    parser.add_argument("--backend", default="http://localhost:8000")
    parser.add_argument("--supabase", default="http://127.0.0.1:54321",
                        help="Supabase's address, for signing in")
    parser.add_argument("--key", help="Supabase's publishable key (default: backend/.env's)")
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--consent", help="consent ID to call with (default: the first usable)")
    parser.add_argument("--scenario", help="scenario ID (default: the first listed)")
    parser.add_argument("--save", type=Path, help="folder to keep the caller's audio in")
    parser.add_argument("--wait-ready", type=float, default=120,
                        help="seconds to wait for the caller's voice to be set up (default 120)")
    args = parser.parse_args()
    try:
        replies = [read_reply(p) for p in args.replies]
        key = args.key or backend_key()
        token = sign_in(args.supabase.rstrip("/"), key, args.email, args.password)
        with httpx.Client(base_url=args.backend, timeout=30,
                          headers={"Authorization": f"Bearer {token}"}) as api:
            call = start_call(api, args.consent, args.scenario)
            print(f"call {call['id']}: {call['persona_name']}, {call['scenario_title']}")
            reason = asyncio.run(talk(call["ws_url"], replies, args.save, args.wait_ready))
            print(f"call ended: {reason}. Waiting for the result...")
            result = wait_result(api, call["id"])
        print(json.dumps({k: result.get(k) for k in (
            "status", "outcome", "score", "duration_seconds", "summary", "failure_reason")},
            indent=2))
        if result.get("status") != "completed":
            sys.exit(1)
    except (CallError, httpx.HTTPError, OSError, asyncio.TimeoutError) as e:
        sys.exit(f"Error: {e or type(e).__name__}")


def backend_key() -> str:
    """SUPABASE_PUBLISHABLE_KEY from backend/.env, read by the backend's own settings."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
    from app.core.config import get_settings

    key = get_settings().supabase_publishable_key
    if key is None:
        raise CallError("no --key given and backend/.env has no SUPABASE_PUBLISHABLE_KEY")
    return key.get_secret_value()


if __name__ == "__main__":
    main()
