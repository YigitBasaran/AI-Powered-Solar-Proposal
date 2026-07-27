#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"

echo "== API =="
cd "$root/apps/api"
python3 -m venv .venv
./.venv/bin/python -m pip install --upgrade pip
./.venv/bin/python -m pip install -e ".[dev,pdf]"
./.venv/bin/python -m playwright install chromium

echo "== Web =="
cd "$root/apps/web"
npm install --no-audit --no-fund

[ -f "$root/.env" ] || cp "$root/.env.example" "$root/.env"

echo
echo "Done. Start with:"
echo "  cd apps/api && ./.venv/bin/python -m uvicorn app.main:app --port 8000"
echo "  cd apps/web && npm run dev"
