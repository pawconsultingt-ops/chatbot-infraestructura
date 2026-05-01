#Requires -Version 5.1
<#
.SYNOPSIS
  Scenario 1 — BASELINE: 1 user, 100 sequential requests.

.DESCRIPTION
  1. Starts sys_monitor.py in background (CPU/RAM/service metrics every 5 s)
  2. Runs Locust headless: 1 user, exits after 100 iterations
  3. Stops sys_monitor
  4. Runs post_process.py to produce consolidated.csv

.PARAMETER Token
  Firebase ID token for authentication. If omitted, reads $env:STRESS_AUTH_TOKEN.
  To get a token: open DevTools on the chat page, run:
      firebase.auth().currentUser.getIdToken().then(t => console.log(t))
#>

param(
    [string]$Token       = $env:STRESS_AUTH_TOKEN,
    [string]$TargetHost        = "http://localhost:8001",
    [int]   $Iterations  = 100,
    [int]   $MonInterval = 5,
    [switch]$WebUI                        # pass -WebUI to open the browser dashboard
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ── paths ──────────────────────────────────────────────────────────────────────
$ScriptDir  = $PSScriptRoot
$ResultsDir = Join-Path $ScriptDir "results\scenario_1_baseline"
$SharedDir  = Join-Path $ScriptDir "shared"
$LocustFile = Join-Path $ScriptDir "scenario_1_baseline\locustfile.py"

New-Item -ItemType Directory -Force -Path $ResultsDir | Out-Null

# ── validation ─────────────────────────────────────────────────────────────────
if (-not $Token) {
    Write-Warning "No auth token provided. Set STRESS_AUTH_TOKEN or pass -Token."
    Write-Warning "Requests will return HTTP 401. Continuing anyway for dry-run."
}

if (-not (Get-Command locust -ErrorAction SilentlyContinue)) {
    Write-Error "locust not found. Run: pip install locust==2.32.2"
}

Write-Host ""
Write-Host "================================================================" -ForegroundColor Cyan
Write-Host "  SCENARIO 1 — BASELINE" -ForegroundColor Cyan
Write-Host "  Users: 1   Iterations: $Iterations   Target: $TargetHost" -ForegroundColor Cyan
Write-Host "================================================================" -ForegroundColor Cyan
Write-Host ""

# ── environment ────────────────────────────────────────────────────────────────
$env:STRESS_AUTH_TOKEN   = $Token
$env:TARGET_HOST         = $TargetHost
$env:BASELINE_ITERATIONS = $Iterations

# ── start sys_monitor ──────────────────────────────────────────────────────────
Write-Host "[1/4] Starting sys_monitor (interval=${MonInterval}s)..." -ForegroundColor Yellow
$MonArgs = @(
    (Join-Path $SharedDir "sys_monitor.py"),
    "--output", $ResultsDir,
    "--interval", $MonInterval,
    "--health-url", "$TargetHost/health"
)
$MonProc = Start-Process "C:\Users\Usuario\AppData\Local\Programs\Python\Python313\python.exe" -ArgumentList $MonArgs -PassThru -NoNewWindow
Write-Host "      PID: $($MonProc.Id)"
Start-Sleep -Seconds 2

# ── run locust ─────────────────────────────────────────────────────────────────
Write-Host "[2/4] Running Locust..." -ForegroundColor Yellow

$LocustArgs = @(
    "-f", $LocustFile,
    "--host", $TargetHost,
    "--users", "1",
    "--spawn-rate", "1",
    "--csv", (Join-Path $ResultsDir "locust"),
    "--html", (Join-Path $ResultsDir "report.html"),
    "--loglevel", "WARNING"
)

if ($WebUI) {
    Write-Host "      Web UI: http://localhost:8089"
    Start-Process locust -ArgumentList $LocustArgs
} else {
    $LocustArgs += "--headless"
    locust @LocustArgs
}

# ── stop sys_monitor ───────────────────────────────────────────────────────────
Write-Host "[3/4] Stopping sys_monitor..." -ForegroundColor Yellow
New-Item -ItemType File -Force -Path (Join-Path $ResultsDir "STOP_MONITOR") | Out-Null
Start-Sleep -Seconds ($MonInterval + 2)
if (-not $MonProc.HasExited) { $MonProc | Stop-Process -Force }

# ── post-process ───────────────────────────────────────────────────────────────
Write-Host "[4/4] Running post_process..." -ForegroundColor Yellow
& "C:\Users\Usuario\AppData\Local\Programs\Python\Python313\python.exe" (Join-Path $SharedDir "post_process.py") --results $ResultsDir --bucket 10

Write-Host ""
Write-Host "================================================================" -ForegroundColor Green
Write-Host "  SCENARIO 1 COMPLETE" -ForegroundColor Green
Write-Host "  Results: $ResultsDir" -ForegroundColor Green
Write-Host "  baseline.json saved - needed by Scenario 2 auto-stop" -ForegroundColor Green
Write-Host "================================================================" -ForegroundColor Green
