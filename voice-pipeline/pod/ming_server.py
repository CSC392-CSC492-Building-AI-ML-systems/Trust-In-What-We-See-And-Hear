"""Ming-3.0-tts behind a small HTTP endpoint, for the call server
(pipeline/tts/served.py, TTS_ENGINE=ming).

It runs in its own environment because its packages clash with Chatterbox's
(pod/voice_engine.sh sets it up). Listens on 127.0.0.1 only:

    /root/venvs/ming/bin/python voice-pipeline/pod/ming_server.py

POST /speak with {"text", "ref_audio" (base64 WAV), "ref_text"} returns a
WAV. The recording is written to a temporary folder for the model to read
and deleted straight after. Nothing about a request is logged: the model's
own logging is turned down because it prints the text it speaks.

    MING_REPO    default /root/ming (a clone of github.com/inclusionAI/Ming-3.0-tts)
    MING_MODEL   default inclusionAI/Ming-3.0-tts-0.5B
    MING_PORT    default 8095

AI-assisted code (written with Claude Code), cited per the CSC398 AI policy.
"""

import base64
import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, os.environ.get("MING_REPO", "/root/ming"))

from loguru import logger  # noqa: E402

logger.remove()
logger.add(sys.stderr, level="WARNING")

from ming_tts import MingTTS  # noqa: E402

try:  # numbers and symbols ("$500") are read out properly only with this
    import pynini  # noqa: F401
    NORMALIZE = True
except ImportError:
    NORMALIZE = False

MODEL = MingTTS(os.environ.get("MING_MODEL", "inclusionAI/Ming-3.0-tts-0.5B"),
                text_normalization=NORMALIZE)
LOCK = threading.Lock()  # one synthesis at a time on the GPU


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.reply(200, b"ok", "text/plain")

    def do_POST(self) -> None:
        if self.path != "/speak":
            self.reply(404, b"not found", "text/plain")
            return
        try:
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            with tempfile.TemporaryDirectory() as folder:
                reference, out = Path(folder, "ref.wav"), Path(folder, "out.wav")
                reference.write_bytes(base64.b64decode(body["ref_audio"]))
                with LOCK:
                    MODEL.synthesize(task="TTS", language_id="English", text=body["text"],
                                     prompt_wav_path=str(reference),
                                     prompt_text=body["ref_text"], use_spk_emb=True,
                                     output_path=str(out))
                audio = out.read_bytes()
        except Exception as e:
            # The error's type only: its text could quote the request.
            self.reply(500, type(e).__name__.encode(), "text/plain")
            return
        self.reply(200, audio, "audio/wav")

    def reply(self, status: int, data: bytes, kind: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args) -> None:
        pass  # no request log


print(f"ming ready (text normalisation {'on' if NORMALIZE else 'off'})", flush=True)
ThreadingHTTPServer(("127.0.0.1", int(os.environ.get("MING_PORT", "8095"))),
                    Handler).serve_forever()
