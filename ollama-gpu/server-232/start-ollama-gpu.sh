#!/bin/sh
set -eu
if pgrep -u "$(id -u)" -x ollama >/dev/null 2>&1; then
  exit 0
fi
export OLLAMA_HOST=0.0.0.0:11434
export OLLAMA_MAX_LOADED_MODELS=1
export OLLAMA_NUM_PARALLEL=1
export OLLAMA_KEEP_ALIVE=5m
export OLLAMA_FLASH_ATTENTION=1
export OLLAMA_KV_CACHE_TYPE=q8_0
export OLLAMA_MODELS=/home/jnicolas/.ollama/models
mkdir -p /home/jnicolas/.local/state/ollama "$OLLAMA_MODELS"
exec nice -n 15 ionice -c 3 /home/jnicolas/.local/bin/ollama serve >>/home/jnicolas/.local/state/ollama/server.log 2>&1
