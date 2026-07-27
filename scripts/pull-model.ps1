# Optional. The application is fully functional without this.
param([string]$Model = "qwen3.5:2b")
$ErrorActionPreference = "Stop"
docker compose --profile ollama up -d ollama
docker compose exec ollama ollama pull $Model
Write-Host "Now set LLM_PROVIDER=ollama in .env and restart the api service."
