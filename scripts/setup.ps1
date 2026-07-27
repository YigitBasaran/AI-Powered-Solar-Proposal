# Windows setup. Uses the py launcher, because `python`/`python3` are often
# shimmed to the Microsoft Store stub on a fresh machine.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

Write-Host "== API ==" -ForegroundColor Cyan
Push-Location "$root/apps/api"
py -3.12 -m venv .venv
& ./.venv/Scripts/python.exe -m pip install --upgrade pip
& ./.venv/Scripts/python.exe -m pip install -e ".[dev,pdf]"
& ./.venv/Scripts/python.exe -m playwright install chromium
Pop-Location

Write-Host "== Web ==" -ForegroundColor Cyan
Push-Location "$root/apps/web"
npm install --no-audit --no-fund
Pop-Location

if (-not (Test-Path "$root/.env")) { Copy-Item "$root/.env.example" "$root/.env" }

Write-Host ""
Write-Host "Done. Start with:" -ForegroundColor Green
Write-Host "  cd apps/api; ./.venv/Scripts/python.exe -m uvicorn app.main:app --port 8000"
Write-Host "  cd apps/web; npm run dev"
