#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Build the submission archive (PowerShell equivalent of build-submission-zip.sh).

.DESCRIPTION
    Uses `git archive` so the contents are exactly what is tracked - which means
    .gitignore is the single definition of what ships, and there is no second
    exclude list to drift out of sync with it. Untracked build output, virtual
    environments, node_modules, databases, .env and Ollama weights are therefore
    excluded by construction rather than by a hand-maintained list.

    This is a real PowerShell implementation, not a wrapper that shells out to
    Bash: on a Windows machine without Git Bash the .sh script cannot run at all,
    and packaging is exactly the step where "it works on my machine" is most
    expensive to get wrong.
#>

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$name = 'solarvis-ai-proposal'
$outDir = Join-Path $root 'submission'
$archive = Join-Path $outDir "$name.zip"

function Fail([string]$message) {
    Write-Host "  FAIL: $message" -ForegroundColor Red
    exit 1
}

New-Item -ItemType Directory -Force -Path $outDir | Out-Null
if (Test-Path $archive) { Remove-Item $archive -Force }

Write-Host '== pre-flight =='

$dirty = git status --porcelain
if ($dirty) {
    Write-Host '  ! working tree is dirty; the archive will reflect HEAD, not your edits' -ForegroundColor Yellow
    git status --short | ForEach-Object { "    $_" }
}

# A committed .env would be a credential leak, and git archive would happily
# include it. Fail loudly rather than shipping it.
git ls-files --error-unmatch .env 2>$null | Out-Null
if ($LASTEXITCODE -eq 0) { Fail '.env is tracked by git. Remove it before packaging.' }

Write-Host '  scanning tracked files for secrets...'
$secretPattern = '(AIza[0-9A-Za-z_-]{30,}|sk-[A-Za-z0-9]{20,}|BEGIN [A-Z ]*PRIVATE KEY)'
$leaks = @()
foreach ($file in (git ls-files)) {
    if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { continue }
    # Skip anything that is not text; a binary match is noise, not a leak.
    $extension = [System.IO.Path]::GetExtension($file)
    if ($extension -in '.png', '.jpg', '.jpeg', '.pdf', '.zip', '.ico', '.woff', '.woff2', '.gguf') { continue }
    try {
        if (Select-String -LiteralPath $file -Pattern $secretPattern -Quiet -ErrorAction Stop) {
            $leaks += $file
        }
    } catch {
        # Unreadable or binary — not a text secret.
    }
}
if ($leaks.Count -gt 0) {
    Write-Host '  FAIL: possible secrets in:' -ForegroundColor Red
    $leaks | ForEach-Object { "    $_" }
    exit 1
}
Write-Host '  no secrets found'

# The sample PDF is a required deliverable and is easy to forget to regenerate.
$samplePdf = Join-Path $root 'sample-output/example-proposal.pdf'
if (-not (Test-Path $samplePdf) -or (Get-Item $samplePdf).Length -eq 0) {
    Fail 'sample-output/example-proposal.pdf is missing or empty. Generate it from the running application first.'
}

Write-Host '== archiving =='
git archive --format=zip --prefix="$name/" -o $archive HEAD
if ($LASTEXITCODE -ne 0) { Fail 'git archive failed' }

$sizeMb = [math]::Round((Get-Item $archive).Length / 1MB, 2)
$trackedCount = (git ls-files | Measure-Object -Line).Lines
Write-Host "  $archive"
Write-Host "  $trackedCount tracked files, $sizeMb MB"

Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = [System.IO.Compression.ZipFile]::OpenRead($archive)
try {
    $entries = $zip.Entries | ForEach-Object { $_.FullName }
} finally {
    $zip.Dispose()
}

Write-Host '== contents sanity =='
$required = @(
    "$name/README.md"
    "$name/docker-compose.yml"
    "$name/.env.example"
    "$name/LICENSE-NOTICE.md"
    "$name/apps/api/pyproject.toml"
    "$name/apps/web/package.json"
    "$name/apps/api/app/data/fixed_roof_calibration.json"
    "$name/fixtures/maps/satellite-fixture.png"
    "$name/sample-output/example-proposal.pdf"
    "$name/docs/location-verification.md"
    "$name/docs/case-questions.md"
)
foreach ($path in $required) {
    if ($entries -contains $path) { Write-Host "  ok   $path" }
    else { Fail "MISSING $path" }
}

Write-Host '== must NOT be present =='
# Anchored patterns. A loose match on "\.env" also matches .env.example, which
# is a required file - the check has to distinguish them.
function Assert-Absent([string]$label, [string]$pattern) {
    $hits = $entries | Where-Object { $_ -match $pattern }
    if ($hits) {
        Write-Host "  FAIL: archive contains $label" -ForegroundColor Red
        $hits | Select-Object -First 5 | ForEach-Object { "    $_" }
        exit 1
    }
    Write-Host "  ok   no $label"
}

Assert-Absent '.env'          '(^|/)\.env$'
Assert-Absent 'node_modules'  '(^|/)node_modules/'
Assert-Absent 'virtualenvs'   '(^|/)\.venv/'
Assert-Absent 'databases'     '\.(db|sqlite3?)$'
Assert-Absent 'model weights' '\.gguf$'
Assert-Absent 'next build'    '(^|/)\.next(-degraded)?/'
Assert-Absent 'python cache'  '(^|/)__pycache__/'

Write-Host ''
Write-Host 'Archive ready. Verify it from a clean extraction with:'
Write-Host '  pwsh scripts/verify-submission.ps1'
