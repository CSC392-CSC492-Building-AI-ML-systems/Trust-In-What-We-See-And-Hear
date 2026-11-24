"""dev/tunnels.py's voice-link filter and dev/backend_call.py's helpers.

AI-assisted code (written with Claude Code), cited per the CSC398 AI policy.
"""

import http.server
import importlib.util
import threading
import urllib.error
import urllib.request
import wave
from pathlib import Path

import pytest

DEV = Path(__file__).parents[1] / "dev"


def load(name):
    spec = importlib.util.spec_from_file_location(name, DEV / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tunnels = load("tunnels")
backend_call = load("backend_call")

SIGNED = "/storage/v1/object/sign/voice-samples/u/sample.wav?token=abc"


@pytest.mark.parametrize("method,path,ok", [
    ("GET", SIGNED, True),
    ("POST", SIGNED, False),
    ("GET", "/storage/v1/object/voice-samples/u/sample.wav", False),  # needs the secret key
    ("GET", "/rest/v1/users", False),
    ("GET", "/auth/v1/admin/users", False),
    ("GET", "/storage/v1/object/sign/../../../rest/v1/users", False),
    ("GET", "/storage/v1/object/sign/%2E%2E/%2e%2e/rest/v1/users", False),
    ("GET", "/storage/v1/object/sign/a%2Fb", False),
])
def test_the_voice_filter_passes_only_signed_downloads(method, path, ok):
    assert tunnels.allowed(method, path) is ok


@pytest.fixture
def supabase():
    """A stand-in for the local Supabase that records what reaches it."""
    seen = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(self.path)
            body = b"RIFF-voice"
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_address[1], seen
    server.shutdown()


def test_the_voice_filter_forwards_a_signed_link(supabase):
    port, seen = supabase
    front = tunnels.voice_filter(port)
    try:
        base = f"http://127.0.0.1:{front.server_address[1]}"
        with urllib.request.urlopen(base + SIGNED, timeout=5) as response:
            assert response.read() == b"RIFF-voice"
            assert response.headers["Content-Type"] == "audio/wav"
        with pytest.raises(urllib.error.HTTPError) as refused:
            urllib.request.urlopen(base + "/rest/v1/users", timeout=5)
        assert refused.value.code == 404
        assert seen == [SIGNED]  # the refused request never reached Supabase
    finally:
        front.shutdown()


def test_the_tunnel_address_is_read_from_cloudflareds_output():
    lines = iter([
        "2026-11-23T19:00:00Z INF Requesting new quick Tunnel on trycloudflare.com...",
        "2026-11-23T19:00:02Z INF |  https://quiet-river-1234.trycloudflare.com  |",
    ])
    assert tunnels.tunnel_address(lines) == "https://quiet-river-1234.trycloudflare.com"
    with pytest.raises(tunnels.TunnelError):
        tunnels.tunnel_address(iter(["ERR failed to request quick Tunnel"]))


def consent(id, direction="outgoing", status="accepted"):
    return {"id": id, "direction": direction, "status": status}


def test_the_call_uses_an_accepted_outgoing_consent():
    items = [consent("a", direction="incoming"), consent("b", status="pending"), consent("c"),
             consent("d")]
    assert backend_call.pick_consent(items, None)["id"] == "c"
    assert backend_call.pick_consent(items, "d")["id"] == "d"
    with pytest.raises(backend_call.CallError):
        backend_call.pick_consent(items, "b")


def write_wav(path, rate=16000, channels=1, width=2, seconds=0.1):
    with wave.open(str(path), "wb") as w:
        w.setframerate(rate)
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.writeframes(b"\0" * int(rate * seconds) * channels * width)


def test_replies_must_be_in_the_microphone_format(tmp_path):
    write_wav(tmp_path / "ok.wav")
    assert len(backend_call.read_reply(tmp_path / "ok.wav")) == 3200
    write_wav(tmp_path / "cd.wav", rate=44100, channels=2)
    with pytest.raises(backend_call.CallError, match="16 kHz mono"):
        backend_call.read_reply(tmp_path / "cd.wav")


def test_the_default_replies_are_in_the_microphone_format():
    assert len(backend_call.DEFAULT_REPLIES) == 3
    for path in backend_call.DEFAULT_REPLIES:
        assert len(backend_call.read_reply(path)) > 16000  # over half a second each


def test_a_refused_call_is_an_error_not_a_traceback():
    # The pod accepts the WebSocket, then closes it with 4401 when the claim fails.
    import asyncio
    from websockets.asyncio.server import serve

    async def refuse(ws):
        await ws.close(4401, "invalid call token")

    async def run():
        async with serve(refuse, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            await backend_call.talk(f"ws://127.0.0.1:{port}", [], None, 5)

    with pytest.raises(backend_call.CallError, match="refused the call's token"):
        asyncio.run(run())

