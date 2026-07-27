#!/usr/bin/env bash
# Optional. The application is fully functional without this.
set -euo pipefail
docker compose --profile ollama up -d ollama
docker compose exec ollama ollama pull "${1:-qwen3.5:2b}"
echo "Now set LLM_PROVIDER=ollama in .env and restart the api service."
