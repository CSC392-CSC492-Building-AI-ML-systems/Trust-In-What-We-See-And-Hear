#!/usr/bin/env bash
# Start the call server on the pod, after setup.sh has finished.
#   PIPELINE_DEV_TOKEN=... PIPELINE_DEV_VOICE=/path/to/voice.wav bash voice-pipeline/pod/run.sh
# or, with a backend to check calls against (see BACKEND.md):
#   PIPELINE_BACKEND_URL=https://... PIPELINE_BACKEND_SECRET=... bash voice-pipeline/pod/run.sh
# Listens on port 8000, which the RunPod image exposes as
# https://<pod id>-8001.proxy.runpod.net (expose 8001/http on the pod).
# Other settings are environment variables, see the top of each module.
#
# AI-assisted code (written with Claude Code), cited per the CSC398 AI policy.
set -euo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd)
VENV=/root/venvs/pipeline
if [ -n "${PIPELINE_BACKEND_URL:-}" ]; then
  : "${PIPELINE_BACKEND_SECRET:?set PIPELINE_BACKEND_SECRET}"
else
  : "${PIPELINE_DEV_TOKEN:?set PIPELINE_DEV_TOKEN}" "${PIPELINE_DEV_VOICE:?set PIPELINE_DEV_VOICE}"
fi
export HF_HOME=/root/hf
export LLM_MODEL=${LLM_MODEL:-$(cat /root/llm_model.txt)}
# faster-whisper needs cuDNN and cuBLAS: reuse the ones torch installed.
LD_LIBRARY_PATH=$($VENV/bin/python -c 'import nvidia.cublas as b, nvidia.cudnn as c; print(b.__path__[0]+"/lib:"+c.__path__[0]+"/lib")')
export LD_LIBRARY_PATH
# No access log: the call's token is in the URL.
exec $VENV/bin/uvicorn --app-dir "$HERE" pipeline.server:app --host 0.0.0.0 --port 8000 --no-access-log
