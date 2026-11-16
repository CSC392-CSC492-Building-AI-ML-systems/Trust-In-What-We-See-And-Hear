#!/usr/bin/env bash
# One-time setup on a fresh GPU pod (RunPod, image
# runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404, container disk >= 60 GB).
# Makes two environments and starts the LLM:
#   /root/venvs/llm       vLLM, serving the LLM on port 8100
#   /root/venvs/pipeline  everything in ../requirements.txt
# Everything goes on the container disk (/root): the /workspace volume is
# network storage, slow for installs and easy to fill.
# Run detached, it takes a few minutes:
#   setsid nohup bash voice-pipeline/pod/setup.sh > /root/setup.log 2>&1 &
#
# AI-assisted code (written with Claude Code), cited per the CSC398 AI policy.
set -euo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd)
export PATH=$HOME/.local/bin:$PATH UV_CACHE_DIR=/root/uv-cache UV_LINK_MODE=copy
export HF_HOME=/root/hf
mkdir -p /root/venvs "$HF_HOME"
command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh

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

# The FP8 model needs nvcc >= 12.9 on RTX 50-series and RTX PRO cards, which
# this image lacks, so those get the full-precision model. vLLM is given
# about 12 GB (FP8) or 20 GB (full) as a share of this GPU's memory.
GPU_GB=$(($(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits | head -1) / 1024))
if nvidia-smi --query-gpu=name --format=csv,noheader | grep -qE "RTX 50|RTX PRO|B200|B300"; then
  LLM_MODEL=${LLM_MODEL:-Qwen/Qwen3-8B}; NEED_GB=20
else
  LLM_MODEL=${LLM_MODEL:-Qwen/Qwen3-8B-FP8}; NEED_GB=12
fi
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
until curl -sf localhost:8100/v1/models >/dev/null; do sleep 5; done
echo "llm up $(date -u +%T)"
