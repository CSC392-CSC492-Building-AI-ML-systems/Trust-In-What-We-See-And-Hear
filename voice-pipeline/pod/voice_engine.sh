#!/usr/bin/env bash
# Installs and starts a voice engine that runs as its own server, for
# TTS_ENGINE=ming or TTS_ENGINE=drama (pipeline/tts/served.py). setup.sh runs
# it after the LLM is up; it can also be run on its own:
#   bash voice-pipeline/pod/voice_engine.sh ming
# Chatterbox needs nothing from here: it runs inside the call server.
#
# AI-assisted code (written with Claude Code), cited per the CSC398 AI policy.
set -euo pipefail
ENGINE=${1:?say which engine: ming or drama}
HERE=$(cd "$(dirname "$0")/.." && pwd)
UV=$HOME/.local/bin/uv  # the current uv setup.sh installed, not the image's
export HF_HOME=/root/hf UV_LINK_MODE=copy UV_CACHE_DIR=/root/uv-cache
export CUDA_HOME=/usr/local/cuda

wait_for() {  # url, process pattern, log
  for i in $(seq 1 180); do
    curl -sf "$1" >/dev/null && return 0
    pgrep -f "$2" >/dev/null || { echo "$ENGINE server stopped; see $3"; tail -20 "$3"; return 1; }
    sleep 5
  done
  echo "$ENGINE server did not answer in 15 minutes; see $3"; return 1
}

case "$ENGINE" in
  ming)
    [ -d /root/ming ] || git clone -q --depth 1 https://github.com/inclusionAI/Ming-3.0-tts /root/ming
    $UV venv -q --python 3.10 /root/venvs/ming
    VIRTUAL_ENV=/root/venvs/ming $UV pip install -q -r /root/ming/requirements.txt
    # Reads numbers and symbols out properly. Optional: Ming runs without it.
    VIRTUAL_ENV=/root/venvs/ming $UV pip install -q pynini==2.1.6 \
      || echo "pynini did not install; Ming will read text as written"
    rm -rf "$UV_CACHE_DIR"
    setsid nohup /root/venvs/ming/bin/python "$HERE/pod/ming_server.py" \
      > /root/ming.log 2>&1 < /dev/null &
    wait_for http://127.0.0.1:8095/ ming_server.py /root/ming.log
    ;;
  drama)
    $UV venv -q --python 3.12 /root/venvs/omni
    VIRTUAL_ENV=/root/venvs/omni $UV pip install -q vllm==0.33.0 ninja --torch-backend=cu130
    # Pinned: vLLM-Omni dropped Drama on 2026-11-18 (#10365, vllm 0.34, no Model
    # Runner V1). This is the last commit before that, built for vllm 0.33.0.
    VIRTUAL_ENV=/root/venvs/omni $UV pip install -q \
      git+https://github.com/vllm-project/vllm-omni.git@ca9398dc9f18b6536daa05fd1980e2e9d370ed4f
    rm -rf "$UV_CACHE_DIR"
    # ninja and nvcc: it compiles a kernel at start-up. Its share of the GPU
    # is set because the LLM is already loaded (it takes 90% by default).
    PATH=/root/venvs/omni/bin:/usr/local/cuda/bin:$PATH setsid nohup \
      vllm serve fishaudio/drama-3 --omni --port 8091 \
      --gpu-memory-utilization "${DRAMA_UTIL:-0.55}" > /root/drama.log 2>&1 < /dev/null &
    wait_for http://127.0.0.1:8091/v1/models "vllm serve fishaudio" /root/drama.log
    ;;
  *) echo "unknown engine: $ENGINE (ming or drama)"; exit 1 ;;
esac
echo "$ENGINE up $(date -u +%T)"
