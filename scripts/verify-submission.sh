#!/usr/bin/env bash
#
# Verify the submission archive from a clean extraction.
#
# Extracts to a throwaway directory and follows the README, so this checks what
# a reviewer actually experiences — not what works in the developer's tree with
# its already-installed virtual environment.
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
name="solarvis-ai-proposal"
archive="$root/submission/$name.zip"

[ -f "$archive" ] || { echo "No archive. Run scripts/build-submission-zip.sh first." >&2; exit 1; }

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
echo "== extracting to $work =="
unzip -q "$archive" -d "$work"
cd "$work/$name"

echo "== structure =="
for path in README.md docker-compose.yml .env.example apps/api apps/web fixtures docs; do
  [ -e "$path" ] && echo "  ok   $path" || { echo "  MISSING $path" >&2; exit 1; }
done

cp .env.example .env

# Probe by *executing* each candidate, not by name resolution. On Windows the
# Microsoft Store ships a `python3` stub that resolves on PATH and then refuses
# to run, so `command -v` reports success for an interpreter that does not work.
pick_python() {
  local candidate
  for candidate in "${PYTHON:-}" "py -3.12" python3.12 python3 python; do
    [ -n "$candidate" ] || continue
    if $candidate -c "import sys; sys.exit(0 if sys.version_info[:2] >= (3, 12) else 1)" \
        >/dev/null 2>&1; then
      echo "$candidate"
      return 0
    fi
  done
  return 1
}

PY="$(pick_python)" || {
  echo "  FAIL: no working Python 3.12+ found." >&2
  echo "        Set PYTHON=/path/to/python and retry." >&2
  exit 1
}
echo "  using interpreter: $PY"

echo "== API: install =="
cd apps/api
$PY -m venv .venv
VENV_PY=".venv/bin/python"
[ -x "$VENV_PY" ] || VENV_PY=".venv/Scripts/python.exe"
"$VENV_PY" -m pip install -q --upgrade pip
"$VENV_PY" -m pip install -q -e ".[dev,pdf]"
# Chromium is what turns the PDF tests from skipped into actually exercised.
"$VENV_PY" -m playwright install chromium >/dev/null 2>&1 ||   echo "  note: chromium install failed; PDF tests will skip"

echo "== API: tests (offline; PVGIS answered by the local replay stub) =="
"$VENV_PY" -m pytest -q -m "not live"

echo "== API: complete a proposal end to end =="
APP_ENV=test FX_MODE=fixture LLM_PROVIDER=rules \
"$VENV_PY" - <<'PYCODE'
import os, tempfile
from pathlib import Path

# PVGIS has no fixture mode: the application always makes a real HTTP call.
# The local replay stub answers it, so this stays offline while exercising the
# same transport, retry ladder and parser a production call would.
import sys
sys.path.insert(0, ".")
from tests.support.pvgis_stub import start_stub

stub_url, _stub, stop_stub = start_stub()

tmp = Path(tempfile.mkdtemp())
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{(tmp / 'verify.db').as_posix()}"
os.environ["PVGIS_BASE_URL"] = f"{stub_url}/api/v5_3"
# The stub is not the canonical PVGIS origin, so what it produces is labelled
# `replay` and is not proposal-grade. Permitted here, and only here, because
# APP_ENV says this is a verification run.
os.environ["ALLOW_REPLAY_PROPOSALS"] = "true"
from app.core.config import get_settings
get_settings.cache_clear()

from fastapi.testclient import TestClient
from app.main import create_app

with TestClient(create_app()) as client:
    pid = client.post("/api/v1/projects").json()["projectId"]
    for message in ("-34.04658, 18.46491", "1,150 kWh", "the middle option"):
        client.post(f"/api/v1/projects/{pid}/chat", json={"message": message})
    analysis = client.post(f"/api/v1/projects/{pid}/run-analysis").json()["analysis"]
    assert analysis["layout"]["placedPanelCount"] == 15, analysis["layout"]
    assert analysis["exchangeRate"]["rate"] != "1"

    token = client.post(f"/api/v1/projects/{pid}/finalize").json()["shareToken"]
    shared = client.get(f"/api/v1/proposals/{token}").json()
    assert shared["financial"]["simplePaybackYears"] > 0

    print(f"  {analysis['layout']['placedPanelCount']} panels, "
          f"{analysis['energy']['totalAnnualProductionKwh']:.0f} kWh, "
          f"payback {shared['financial']['simplePaybackYears']:.2f} yr")
    print(f"  share token {token[:12]}...")

stop_stub()
PYCODE

echo "== sample PDF =="
cd "$work/$name"
head -c 5 sample-output/example-proposal.pdf | grep -q "%PDF-" \
  && echo "  ok   sample-output/example-proposal.pdf is a real PDF" \
  || { echo "  FAIL: sample PDF is not a PDF" >&2; exit 1; }

echo "== web: install, typecheck, build =="
cd apps/web
npm install --no-audit --no-fund --silent
npm run typecheck
npm run build

echo
echo "VERIFIED: the archive installs, tests, completes a proposal and builds"
echo "from a clean extraction."
echo
echo "Not covered here (needs a browser and both servers running):"
echo "  - Playwright E2E: cd apps/web && npm run test:e2e"
echo "  - PDF generation: needs 'playwright install chromium' in apps/api"
