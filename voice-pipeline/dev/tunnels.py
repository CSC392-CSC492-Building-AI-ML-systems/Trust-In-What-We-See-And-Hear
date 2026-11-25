#!/usr/bin/env python3
"""Give a backend on this computer public addresses a GPU pod can reach.

The pod runs on RunPod's network, so it cannot open http://localhost. This
starts two Cloudflare quick tunnels (free, no account): one to the backend,
and one for the voice links the pod downloads from the local Supabase. It
prints the settings to use and keeps the tunnels open until you press Ctrl+C.

The second tunnel does not reach Supabase itself: a local Supabase uses
well-known default keys, so its whole API must not be public. It goes to a
small filter here that passes on only downloads through signed Storage links
(GET /storage/v1/object/sign/...), which work only with their own token.

    python voice-pipeline/dev/tunnels.py
    python voice-pipeline/dev/tunnels.py --no-supabase   the backend uses the hosted Supabase

Needs cloudflared (macOS: brew install cloudflared; Windows: winget install
Cloudflare.cloudflared; Linux: see Cloudflare's downloads page). The
addresses change every time this starts. Anyone who has them can reach the
backend while it runs, so share them only with the team. See voice-pipeline/dev/POD.md.

AI-assisted code (written with Claude Code), cited per the CSC398 AI policy.
"""

from __future__ import annotations

import argparse
import http.server
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

ADDRESS = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
SIGNED = "/storage/v1/object/sign/"  # the only Supabase path the voice tunnel serves


class TunnelError(Exception):
    pass


def tunnel_address(lines, timeout: float = 60) -> str:
    """The public address cloudflared prints, from its output lines."""
    deadline = time.time() + timeout
    for line in lines:
        found = ADDRESS.search(line)
        if found:
            return found.group(0)
        if time.time() > deadline:
            break
    raise TunnelError("cloudflared did not print a tunnel address")


class Tunnel:
    def __init__(self, name: str, port: int):
        self.name, self.port = name, port
        self.process: subprocess.Popen | None = None
        self.address = ""

    def start(self, cloudflared: str) -> str:
        self.process = subprocess.Popen(
            [cloudflared, "tunnel", "--no-autoupdate", "--url", f"http://localhost:{self.port}"],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, bufsize=1)
        self.address = tunnel_address(iter(self.process.stderr.readline, ""))
        # Keep reading so cloudflared never blocks on a full pipe.
        threading.Thread(target=lambda: [None for _ in self.process.stderr], daemon=True).start()
        return self.address

    def stop(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(10)
            except subprocess.TimeoutExpired:
                self.process.kill()


def allowed(method: str, path: str) -> bool:
    """Whether the voice filter passes this request on to Supabase."""
    lowered = path.lower()
    return (method == "GET" and path.startswith(SIGNED)
            and not any(bad in lowered for bad in ("..", "%2e", "%2f", "%5c", "\\")))


def voice_filter(supabase_port: int) -> http.server.ThreadingHTTPServer:
    """A local server that passes signed Storage downloads to Supabase and refuses the rest."""

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if not allowed("GET", self.path):
                self.send_error(404)
                return
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{supabase_port}{self.path}",
                                            timeout=30) as upstream:
                    self.send_response(upstream.status)
                    for name in ("Content-Type", "Content-Length"):
                        if upstream.headers.get(name):
                            self.send_header(name, upstream.headers[name])
                    self.end_headers()
                    shutil.copyfileobj(upstream, self.wfile)
            except urllib.error.HTTPError as e:
                self.send_error(e.code)
            except OSError:
                self.send_error(502, "the local Supabase did not answer")

        def do_POST(self) -> None:
            self.send_error(404)

        do_PUT = do_DELETE = do_PATCH = do_HEAD = do_POST

        def log_message(self, *args) -> None:
            pass  # the paths carry the links' tokens

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def answers(url: str, timeout: float = 90) -> int:
    """The HTTP status the address gives, once its DNS name works (new ones take a moment)."""
    deadline = time.time() + timeout
    while True:
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "voice-pipeline-tunnels"})
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status
        except urllib.error.HTTPError as e:
            if e.code not in (502, 503, 530) or time.time() > deadline:
                return e.code
        except OSError:
            if time.time() > deadline:
                return 0
        time.sleep(3)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--backend-port", type=int, default=8000)
    parser.add_argument("--supabase-port", type=int, default=54321,
                        help="the local Supabase's API port (default 54321)")
    parser.add_argument("--no-supabase", action="store_true",
                        help="no Supabase tunnel: the backend uses the hosted Supabase")
    args = parser.parse_args()

    # Closing the window or `kill` should close the tunnels too, like Ctrl+C.
    for name in ("SIGTERM", "SIGHUP"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), signal.default_int_handler)
    cloudflared = shutil.which("cloudflared")
    if not cloudflared:
        sys.exit("cloudflared is not installed. macOS: brew install cloudflared; "
                 "Windows: winget install Cloudflare.cloudflared")
    tunnels = [Tunnel("backend", args.backend_port)]
    if not args.no_supabase:
        tunnels.append(Tunnel("voice links", voice_filter(args.supabase_port).server_address[1]))
    try:
        for tunnel in tunnels:
            print(f"Opening a tunnel to the {tunnel.name} (localhost:{tunnel.port})...", flush=True)
            tunnel.start(cloudflared)
        backend = tunnels[0].address
        status = answers(f"{backend}/health")
        if status != 200:
            print(f"Warning: the backend did not answer through its tunnel (got {status or 'nothing'}). "
                  f"Is it running on port {args.backend_port}?")
        print()
        print("=" * 70)
        print("Tunnels are open. Keep this window open while you test; Ctrl+C closes them.")
        print()
        print("1. Start the pod against this backend:")
        print(f"     python voice-pipeline/dev/pod.py start --backend {backend}")
        print()
        print("2. Then set these in backend/.env (pod.py prints the first two) and restart the")
        print("   backend with `npm run dev -- --pipeline`:")
        print("     PIPELINE_WS_URL=wss://<pod id>-8001.proxy.runpod.net")
        print("     PIPELINE_BACKEND_SECRET=<the secret pod.py names when the pod is ready>")
        if len(tunnels) > 1:
            print(f"     PIPELINE_SUPABASE_URL={tunnels[1].address}")
        print()
        print("These addresses change each time this starts; a pod started with an old one")
        print("cannot reach the backend. Share them only with the team.")
        print("=" * 70, flush=True)
        while all(t.process.poll() is None for t in tunnels):
            time.sleep(2)
        print("A tunnel closed by itself; closing the other. Start this again.")
        sys.exit(1)
    except TunnelError as e:
        sys.exit(f"Error: {e}.")
    except KeyboardInterrupt:
        print("\nClosing the tunnels.")
    finally:
        for tunnel in tunnels:
            tunnel.stop()


if __name__ == "__main__":
    main()
