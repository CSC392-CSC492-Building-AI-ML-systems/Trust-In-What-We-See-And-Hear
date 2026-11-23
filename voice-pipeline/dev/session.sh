#!/usr/bin/env bash
# Development tool. Runs on the pod for one test session. pod.py starts it; nobody needs to run
# it by hand. It:
#   1. installs everything and starts the LLM (setup.sh), reporting progress
#   2. starts the call server with the browser test pages (run.sh)
#   3. terminates the pod after IDLE_MINUTES without a call, so a forgotten
#      pod stops billing
# Progress, in plain words, goes to /root/session.log and the container log
# (pod.py shows it). The
# current step is in /root/stage: setup, server, ready or failed.
#
# Settings (pod.py sets them): PIPELINE_DEV_TOKEN, TTS_ENGINE, LLM_MODEL,
# VLLM_UTIL, IDLE_MINUTES (default 30).
#
# AI-assisted code (written with Claude Code), cited per the CSC398 AI policy.
set -uo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd)
IDLE_MINUTES=${IDLE_MINUTES:-30}
ENGINE=${TTS_ENGINE:-ming}
# Chatterbox runs inside the call server; the others need a step of their own.
STEPS=3; [ "$ENGINE" = chatterbox ] || STEPS=4
VOICE=/root/voice.wav
# setup.sh's uv cache: uv downloads there and fills /root/venvs only at the
# end, so the install progress counts both.
UV_CACHE=/root/uv-cache

# Each line goes to session.log and to the pod's container log, which RunPod's
# API serves to any teammate's key (pod.py status and logs read it there).
say() {
  local line="[$(date -u +%H:%M:%S)] $*"
  echo "$line"
  echo "[voice-pipeline] $line" > /proc/1/fd/1 2>/dev/null || true
}
stage() { echo "$1" > /root/stage; }
size_gb() { du -sm "$@" 2>/dev/null | awk '{s+=$1} END {printf "%.1f", s/1024}'; }

# The pod's own ID and API key (RunPod sets both in every pod). A command
# started over SSH does not inherit them, so read them from PID 1.
from_pid1() { tr '\0' '\n' < /proc/1/environ 2>/dev/null | sed -n "s/^$1=//p" | head -1; }
POD_ID=${RUNPOD_POD_ID:-$(from_pid1 RUNPOD_POD_ID)}
POD_KEY=${RUNPOD_API_KEY:-$(from_pid1 RUNPOD_API_KEY)}

terminate_self() {
  say "Shutting the pod down: $1"
  curl -sf -X DELETE "https://api.runpod.io/v2/pods/$POD_ID" \
       -H "Authorization: Bearer $POD_KEY" >/dev/null && exit 0
  command -v runpodctl >/dev/null && runpodctl pod delete "$POD_ID" && exit 0
  # Stopping still frees the GPU, which is what bills.
  curl -sf -X POST "https://api.runpod.io/v2/pods/$POD_ID/action" \
       -H "Authorization: Bearer $POD_KEY" -H "Content-Type: application/json" \
       -d '{"action":"stop"}' >/dev/null && exit 0
  say "Could not shut the pod down by itself. Stop it with: python voice-pipeline/dev/pod.py stop"
}

# Watches for idle time in the background, from the start, so a pod whose
# setup fails also goes away. Setup itself counts as busy, for up to 90 min.
watch_idle() {
  local limit=$((IDLE_MINUTES * 60)) started=$SECONDS quiet_from=$SECONDS
  while sleep 60; do
    case $(cat /root/stage 2>/dev/null) in
      setup|server)
        quiet_from=$SECONDS
        [ $((SECONDS - started)) -gt 5400 ] && terminate_self "setup took over 90 minutes" ;;
      ready)
        idle=$(curl -sf --max-time 10 localhost:8000/health \
               | python3 -c 'import json,sys; print(json.load(sys.stdin)["idle_seconds"])' 2>/dev/null)
        if [ -n "$idle" ]; then quiet_from=$((SECONDS - idle)); fi
        # If the server stopped answering, quiet_from stays where it was.
        [ $((SECONDS - quiet_from)) -ge $limit ] && terminate_self "no calls for $IDLE_MINUTES minutes" ;;
      *)  # failed
        [ $((SECONDS - quiet_from)) -ge $limit ] && terminate_self "setup failed $IDLE_MINUTES minutes ago" ;;
    esac
  done
}

# The reason goes to the log before the stage says failed: pod.py stops
# reading as soon as it sees the stage.
fail() {
  say "FAILED: $1"
  say "The last lines of $2:"
  tail -n 25 "$2" | while IFS= read -r l; do say "    $l"; done
  if [ "$IDLE_MINUTES" -gt 0 ]; then
    say "The pod shuts itself down in $IDLE_MINUTES minutes. To stop it now: python voice-pipeline/dev/pod.py stop"
  else
    say "The pod does not shut itself down (IDLE_MINUTES=0). Stop it with: python voice-pipeline/dev/pod.py stop"
  fi
  stage failed
  wait
  exit 1
}

stage setup
if [ -n "$POD_ID" ] && [ -n "$POD_KEY" ] && [ "$IDLE_MINUTES" -gt 0 ]; then
  watch_idle &
else
  say "Note: automatic shutdown is off (no pod ID or key found, or IDLE_MINUTES=0)."
fi

say "Step 1 of $STEPS: installing the Python packages. This takes about 5 minutes."
TTS_ENGINE=$ENGINE setsid bash "$HERE/pod/setup.sh" > /root/setup.log 2>&1 < /dev/null &
SETUP=$!
step=1 last_report=$SECONDS
while kill -0 $SETUP 2>/dev/null; do
  sleep 5
  if [ $step = 1 ] && grep -q "^LLM: " /root/setup.log; then
    step=2
    say "Packages installed."
    say "Step 2 of $STEPS: downloading and loading the language model ($(cat /root/llm_model.txt 2>/dev/null))."
    say "It is about 8 to 16 GB; this takes 5 to 10 minutes."
  elif [ $step = 2 ] && grep -q "^llm up" /root/setup.log; then
    step=3
    say "The language model is running."
    if [ "$ENGINE" = ming ] || [ "$ENGINE" = drama ]; then
      say "Step 3 of $STEPS: installing and loading the $ENGINE voice engine. This takes 5 to 15 minutes."
    fi
  fi
  if [ $((SECONDS - last_report)) -ge 30 ]; then
    last_report=$SECONDS
    case $step in
      1) say "  still installing: $(size_gb $UV_CACHE /root/venvs) GB of packages so far" ;;
      2) now=$(size_gb /root/hf)
         if [ "$now" = "${last_size:-}" ] && [ "$now" != 0.0 ]; then
           say "  downloaded ($now GB); loading it onto the GPU"
         else
           say "  still downloading the language model: $now GB so far"
         fi
         last_size=$now ;;
      3) [ "$ENGINE" != chatterbox ] && say "  still setting up the voice engine ($(size_gb $UV_CACHE /root/venvs /root/hf) GB on disk)" ;;
    esac
  fi
done
wait $SETUP || fail "the setup step stopped with an error." /root/setup.log

stage server
say "Step $STEPS of $STEPS: starting the call server. Loading the speech models takes 1 to 3 minutes."
cd /root/app
PIPELINE_DEV_VOICE=$VOICE PIPELINE_TEST_PAGE=$HERE/dev/call.html TTS_ENGINE=$ENGINE \
  setsid nohup bash "$HERE/pod/run.sh" > /root/server.log 2>&1 < /dev/null &
for i in $(seq 1 120); do
  curl -sf localhost:8000/health >/dev/null && break
  pgrep -f "uvicorn.*pipeline.server" >/dev/null || [ "$i" -lt 6 ] \
    || fail "the call server stopped while starting." /root/server.log
  [ $((i % 6)) = 0 ] && say "  still loading the speech models"
  sleep 5
done
curl -sf localhost:8000/health >/dev/null || fail "the call server did not start in 10 minutes." /root/server.log

stage ready
say "READY"
wait
