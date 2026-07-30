#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Verify the submission archive from a clean extraction (PowerShell equivalent
    of verify-submission.sh).

.DESCRIPTION
    Extracts to a throwaway directory and follows the README, so this checks
    what a reviewer actually experiences - not what works in the developer's
    tree with its already-installed virtual environment.

.PARAMETER Python
    Interpreter to use for the API. Defaults to probing for a working 3.12+.

.PARAMETER SkipWeb
    Skip the npm install/typecheck/build stage. Useful when only the API side
    changed; the full run is what the submission claim rests on.
#>

param(
    [string]$Python = $env:PYTHON,
    [switch]$SkipWeb
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$root = Split-Path -Parent $PSScriptRoot
$name = 'solarvis-ai-proposal'
$archive = Join-Path $root "submission/$name.zip"

function Fail([string]$message) {
    Write-Host "  FAIL: $message" -ForegroundColor Red
    exit 1
}

if (-not (Test-Path $archive)) {
    Fail 'No archive. Run scripts/build-submission-zip.ps1 first.'
}

$work = Join-Path ([System.IO.Path]::GetTempPath()) ("solarvis-verify-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Force -Path $work | Out-Null

try {
    Write-Host "== extracting to $work =="
    Expand-Archive -LiteralPath $archive -DestinationPath $work -Force
    $project = Join-Path $work $name
    Set-Location $project

    Write-Host '== structure =='
    foreach ($path in 'README.md', 'docker-compose.yml', '.env.example', 'apps/api', 'apps/web', 'fixtures', 'docs') {
        if (Test-Path $path) { Write-Host "  ok   $path" } else { Fail "MISSING $path" }
    }

    Copy-Item .env.example .env

    # Probe by *executing* each candidate, not by name resolution. Windows ships
    # a Microsoft Store `python3` stub that resolves on PATH and then refuses to
    # run, so a name lookup reports success for an interpreter that does not work.
    function Resolve-Python {
        $candidates = @()
        if ($Python) { $candidates += , @($Python) }
        $candidates += , @('py', '-3.12')
        $candidates += , @('python3.12')
        $candidates += , @('python')
        $candidates += , @('python3')

        foreach ($candidate in $candidates) {
            $exe = $candidate[0]
            $prefixArgs = @($candidate | Select-Object -Skip 1)
            try {
                $probe = @($prefixArgs) + @('-c', 'import sys; sys.exit(0 if sys.version_info[:2] >= (3, 12) else 1)')
                & $exe @probe 2>$null
                if ($LASTEXITCODE -eq 0) { return , @($exe, $prefixArgs) }
            } catch {
                continue
            }
        }
        return $null
    }

    $resolved = Resolve-Python
    if (-not $resolved) {
        Fail 'no working Python 3.12+ found. Pass -Python <path> and retry.'
    }
    $pyExe = $resolved[0]
    $pyArgs = $resolved[1]
    Write-Host "  using interpreter: $pyExe $($pyArgs -join ' ')"

    Write-Host '== API: install =='
    Set-Location (Join-Path $project 'apps/api')
    & $pyExe @($pyArgs + @('-m', 'venv', '.venv'))
    if ($LASTEXITCODE -ne 0) { Fail 'venv creation failed' }

    $venvPy = Join-Path (Get-Location) '.venv/Scripts/python.exe'
    if (-not (Test-Path $venvPy)) { $venvPy = Join-Path (Get-Location) '.venv/bin/python' }
    if (-not (Test-Path $venvPy)) { Fail 'no interpreter inside the new virtual environment' }

    & $venvPy -m pip install -q --upgrade pip
    & $venvPy -m pip install -q -e '.[dev,pdf]'
    if ($LASTEXITCODE -ne 0) { Fail 'pip install failed' }

    # Chromium is what turns the PDF tests from skipped into actually exercised.
    & $venvPy -m playwright install chromium *> $null
    if ($LASTEXITCODE -ne 0) { Write-Host '  note: chromium install failed; PDF tests will skip' -ForegroundColor Yellow }

    Write-Host '== API: tests (offline; PVGIS answered by the local replay stub) =='
    & $venvPy -m pytest -q -m 'not live'
    if ($LASTEXITCODE -ne 0) { Fail 'API tests failed' }

    Write-Host '== API: complete a proposal end to end =='
    $env:APP_ENV = 'test'
    $env:FX_MODE = 'fixture'
    $env:LLM_PROVIDER = 'rules'
    # PVGIS has no fixture mode; the inline script starts the replay stub and
    # points PVGIS_BASE_URL at it, so this stays offline while making a real call.
    $env:ALLOW_REPLAY_PROPOSALS = 'true'

    $script = @'
import os, sys, tempfile
from pathlib import Path

sys.path.insert(0, ".")
from tests.support.pvgis_stub import start_stub

stub_url, _stub, stop_stub = start_stub()
os.environ["PVGIS_BASE_URL"] = f"{stub_url}/api/v5_3"

tmp = Path(tempfile.mkdtemp())
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{(tmp / 'verify.db').as_posix()}"
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
'@
    $scriptFile = Join-Path $work 'verify_flow.py'
    Set-Content -LiteralPath $scriptFile -Value $script -Encoding utf8
    & $venvPy $scriptFile
    if ($LASTEXITCODE -ne 0) { Fail 'end-to-end proposal check failed' }

    Write-Host '== sample PDF =='
    Set-Location $project
    $pdfPath = Join-Path $project 'sample-output/example-proposal.pdf'
    $magic = [System.IO.File]::ReadAllBytes($pdfPath)[0..4]
    $header = -join ($magic | ForEach-Object { [char]$_ })
    if ($header -ne '%PDF-') { Fail 'sample PDF is not a PDF' }
    Write-Host '  ok   sample-output/example-proposal.pdf is a real PDF'

    if ($SkipWeb) {
        Write-Host '== web: skipped (-SkipWeb) ==' -ForegroundColor Yellow
    } else {
        Write-Host '== web: install, typecheck, build =='
        Set-Location (Join-Path $project 'apps/web')
        npm install --no-audit --no-fund --silent
        if ($LASTEXITCODE -ne 0) { Fail 'npm install failed' }
        npm run typecheck
        if ($LASTEXITCODE -ne 0) { Fail 'typecheck failed' }
        npm run build
        if ($LASTEXITCODE -ne 0) { Fail 'next build failed' }
    }

    Write-Host ''
    Write-Host 'VERIFIED: the archive installs, tests, completes a proposal and builds' -ForegroundColor Green
    Write-Host 'from a clean extraction.'
    Write-Host ''
    Write-Host 'Not covered here (needs a browser and both servers running):'
    Write-Host '  - Playwright E2E: cd apps/web; npm run test:e2e'
    Write-Host "  - PDF generation: needs 'playwright install chromium' in apps/api"
} finally {
    Set-Location $root
    Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
}
