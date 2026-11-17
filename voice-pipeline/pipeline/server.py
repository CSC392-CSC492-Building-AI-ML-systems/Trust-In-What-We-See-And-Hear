"""The real call server. Needs a GPU and the packages in requirements.txt.

    uvicorn --app-dir voice-pipeline pipeline.server:app --host 0.0.0.0 --port 8000

Loads the models once at start-up (about a minute), then serves the same
WebSocket as mock_server.py. Expects the LLM to be running already (llm.py).
Settings are environment variables, documented at the top of each module.

    TTS_ENGINE          chatterbox (default) or turbo (tts/chatterbox.py), or
                        ming or drama, which run as their own server
                        (tts/served.py, pod/voice_engine.sh)
    PIPELINE_TEST_PAGE  path to an HTML page to serve at /, for testing
                        without the app

AI-assisted code (written with Claude Code), cited per the CSC398 AI policy.
"""

from __future__ import annotations

import logging
import os

import numpy as np

from .calls import BackendCalls, DevCalls
from .config import Settings
from .llm import ChatLLM
from .models import Models
from .segmenter import VADSegmenter
from .session import create_app
from .stt import RobustSTT
from .tts.chatterbox import ChatterboxEngine
from .tts.served import DramaEngine, MingEngine

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
log = logging.getLogger("pipeline.server")


def warm_up(stt: RobustSTT, tts: ChatterboxEngine) -> None:
    """Run each model once so the first call does not pay for it.

    Speech to text is warmed up with real speech (the voice engine's own
    output): silence skips the decoder, and on GPUs newer than CTranslate2's
    build the first real call compiled kernels for about 10 s.
    """
    voice = tts.builtin_voice()
    if voice is None:
        log.warning("the voice engine has no built-in voice; skipping warm-up")
        return
    pieces = list(tts.speak("Hey, it's me. Can you hear me okay? I need to ask you something.",
                            voice, first_in_turn=True))
    speech = np.frombuffer(b"".join(p.pcm16 for p in pieces), dtype="<i2").astype(np.float32) / 32768
    rate = tts.info.sample_rate
    at_16k = np.interp(np.arange(0, len(speech), rate / 16000),
                       np.arange(len(speech)), speech).astype(np.float32)
    for _ in range(2):
        stt.transcribe(at_16k)


def transcriber(stt: RobustSTT):
    """Recording path -> the words in it, for engines that clone from both."""
    import soundfile

    def transcribe(path: str) -> str:
        audio, rate = soundfile.read(path, dtype="float32", always_2d=True)
        audio = audio.mean(axis=1)
        at_16k = np.interp(np.arange(0, len(audio), rate / 16000),
                           np.arange(len(audio)), audio).astype(np.float32)
        return stt.transcribe(at_16k).text  # not logged: stt logs numbers only

    return transcribe


def make_tts(stt: RobustSTT):
    """The voice engine named by TTS_ENGINE (see parts.TTSEngine)."""
    engine = os.environ.get("TTS_ENGINE", "chatterbox")
    if engine == "ming":
        return MingEngine.from_env(transcriber(stt))
    if engine == "drama":
        return DramaEngine.from_env(transcriber(stt))
    return ChatterboxEngine.from_env()  # chatterbox or turbo


def load_models() -> Models:
    log.info("loading models...")
    stt = RobustSTT.from_env()
    tts = make_tts(stt)
    warm_up(stt, tts)
    VADSegmenter()  # loads the speech detector now, not on the first call
    log.info("models ready (voice engine: %s)", tts.info.name)
    return Models(stt=stt, tts=tts, llm=ChatLLM.from_env(), new_segmenter=VADSegmenter)


if os.environ.get("PIPELINE_BACKEND_URL"):
    calls = BackendCalls.from_env()
    log.info("calls are checked with the backend")
    if not calls.secret:
        log.warning("PIPELINE_BACKEND_SECRET is not set: the backend will refuse us")
else:
    calls = DevCalls.from_env()
    if not calls.token:
        log.warning("PIPELINE_DEV_TOKEN is not set: every connection will be refused")
app = create_app(load_models(), calls, Settings.from_env())

if os.environ.get("PIPELINE_TEST_PAGE"):
    # A browser page for trying calls without the app (testing only): it
    # talks to this server, so anyone with its address and the token can call.
    from fastapi.responses import FileResponse

    @app.get("/", include_in_schema=False)
    def test_page() -> FileResponse:
        return FileResponse(os.environ["PIPELINE_TEST_PAGE"])
