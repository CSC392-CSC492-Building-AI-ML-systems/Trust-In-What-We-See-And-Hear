#!/usr/bin/env bash
# One-time setup on a fresh GPU pod (RunPod, image
# runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404, container disk >= 60 GB).
# Makes two environments and starts the LLM:
#   /root/venvs/llm       vLLM, serving the LLM on port 8100
#   /root/venvs/pipeline  everything in ../requirements.txt
# Everything goes on the container disk (/root): the /workspace volume is
# network storage, slow for installs and easy to fill.
# With TTS_ENGINE=ming or drama it also sets up that voice engine
# (voice_engine.sh). Drama 3 needs an 80 GB GPU and about 150 GB of disk.
# Run detached, it takes a few minutes:
#   setsid nohup bash voice-pipeline/pod/setup.sh > /root/setup.log 2>&1 &
# Choose the LLM with LLM_MODEL=... (the script refuses one that cannot fit).
#
# AI-assisted code (written with Claude Code), cited per the CSC398 AI policy.
set -euo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd)
export PATH=$HOME/.local/bin:$PATH UV_CACHE_DIR=/root/uv-cache UV_LINK_MODE=copy
export HF_HOME=/root/hf
mkdir -p /root/venvs "$HF_HOME"
# The FP8 model needs nvcc >= 12.9 on RTX 50-series and RTX PRO cards, which
# this image lacks, so those get the full-precision model. vLLM is given
# about 12 GB (FP8) or 20 GB (full) as a share of this GPU's memory.
GPU_GB=$(($(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits | head -1) / 1024))
if nvidia-smi --query-gpu=name --format=csv,noheader | grep -qE "RTX 50|RTX PRO|B200|B300"; then
  LLM_MODEL=${LLM_MODEL:-Qwen/Qwen3-8B}; NEED_GB=20
else
  LLM_MODEL=${LLM_MODEL:-Qwen/Qwen3-8B-FP8}; NEED_GB=12
fi
# A bigger model (LLM_MODEL=...) needs more. The speech models need about
# 8 GB of what is left. On 2026-11-17 a 32B model was tried on a 48 GB card
# and failed to load, so a model that cannot fit is refused here.
case "$LLM_MODEL" in
  *14B*) NEED_GB=$((NEED_GB * 2 + 1)) ;;
  *30B*|*32B*) NEED_GB=$((NEED_GB * 10 / 3)) ;;
esac
# The voice engine needs the rest: about 8 GB with speech to text, or about
# 48 GB for Fish Audio Drama 3, which runs as its own server.
SPEECH_GB=8
[ "${TTS_ENGINE:-}" = drama ] && SPEECH_GB=48
if [ $((GPU_GB - NEED_GB)) -lt $SPEECH_GB ]; then
  echo "$LLM_MODEL needs about $NEED_GB GB and the voice about $SPEECH_GB GB: more than this ${GPU_GB} GB GPU has."
  echo "Use a smaller model, or a GPU with more memory."
  exit 1
fi
export DRAMA_UTIL=$(python3 -c "print(round(44 / $GPU_GB, 2))")
# Always a current uv, in ~/.local/bin (first on PATH). The image ships an
# old one that rejected newer CUDA builds on 2026-11-17.
curl -LsSf https://astral.sh/uv/install.sh | sh

# ninja: vLLM compiles a kernel at start-up.
(uv venv -q --python 3.12 /root/venvs/llm
 VIRTUAL_ENV=/root/venvs/llm uv pip install -q vllm ninja
 echo "venv llm ready $(date -u +%T)") &
# torch 2.8 works on every GPU we have used, including RTX 50-series.
printf 'torch==2.8.0\ntorchaudio==2.8.0\n' > /root/overrides.txt
(uv venv -q --python 3.11 /root/venvs/pipeline
 VIRTUAL_ENV=/root/venvs/pipeline UV_OVERRIDE=/root/overrides.txt \
   uv pip install -q -r "$HERE/requirements.txt"
 echo "venv pipeline ready $(date -u +%T)") &
wait
# The download cache is about 17 GB and nothing needs it now. On 2026-11-17
# a full disk made a model download fail.
rm -rf "$UV_CACHE_DIR"

SHARE=${VLLM_UTIL:-$(python3 -c "print(round(max(0.3, min(0.85, $NEED_GB / $GPU_GB)), 2))")}
echo "$LLM_MODEL" > /root/llm_model.txt
cat > /root/vllm.sh <<EOF
#!/usr/bin/env bash
export HF_HOME=/root/hf CUDA_HOME=/usr/local/cuda
export PATH=/root/venvs/llm/bin:/usr/local/cuda/bin:\$PATH
exec vllm serve $LLM_MODEL --port 8100 --max-model-len 4096 --gpu-memory-utilization $SHARE
EOF
echo "LLM: $LLM_MODEL, $SHARE of ${GPU_GB} GB"
setsid nohup bash /root/vllm.sh > /root/vllm.log 2>&1 < /dev/null &
sleep 10  # vllm.sh replaces itself with `vllm serve`; give it a moment
until curl -sf localhost:8100/v1/models >/dev/null; do
  if ! pgrep -f "vllm serve" >/dev/null; then
    echo "the LLM server stopped while loading. The end of /root/vllm.log:"; tail -n 20 /root/vllm.log
    exit 1
  fi
  sleep 5
done
echo "llm up $(date -u +%T)"
# Only once the LLM is loaded: two engines loading at once misjudge how much
# GPU memory is free, and one of them fails.
case "${TTS_ENGINE:-chatterbox}" in
  ming|drama) bash "$HERE/pod/voice_engine.sh" "$TTS_ENGINE" ;;
esac
echo "setup done $(date -u +%T)"
