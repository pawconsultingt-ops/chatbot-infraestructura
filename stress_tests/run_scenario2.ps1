#Requires -Version 5.1
<#
.SYNOPSIS
  Scenario 2 — RAMP: 1→100 users (+5 every 60 s), auto-stop at breakpoint.

.DESCRIPTION
  Reads baseline p95 from results/scenario_1_baseline/baseline.json.
  Auto-stops when:
    - p95 > 3× baseline  OR
    - Error rate > 5%    OR
    - Max users (100) reached
  Writes breakpoint.json — used by Scenario 3 to set target concurrency.
#>

param(
    [string]$Token        = $env:STRESS_AUTH_TOKEN,
    [string]$Host         = "http://localhost:8001",
    [int]   $StepUsers    = 5,
    [int]   $StepDuration = 60,
    [int]   $MaxUsers     = 100,
    [float] $LatencyMult  = 3.0,
    [float] $ErrorThresh  = 0.05,
    [int]   $MonInterval  = 5,
    [switch]$WebUI
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ScriptDir  = $PSScriptRoot
$ResultsDir = Join-Path $ScriptDir "results\scenario_2_ramp"
$SharedDir  = Join-Path $ScriptDir "shared"
$LocustFile = Join-Path $ScriptDir "scenario_2_ramp\locustfile.py"

New-Item -ItemType Directory -Force -Path $ResultsDir | Out-Null

# Warn if baseline not available
$BaselineFile = Join-Path $ScriptDir "results\scenario_1_baseline\baseline.json"
if (-not (Test-Path $BaselineFile)) {
    Write-Warning "baseline.json not found at $BaselineFile"
    Write-Warning "Run run_scenario1.ps1 first for latency-based auto-stop."
    Write-Warning "Only error-rate stop criterion will be active."
}

if (-not $Token) {
    Write-Warning "No auth token provided. Requests will return HTTP 401."
}

Write-Host ""
Write-Host "================================================================" -ForegroundColor Cyan
Write-Host "  SCENARIO 2 — RAMP (find the breaking point)" -ForegroundColor Cyan
Write-Host "  Shape: +$StepUsers users every ${StepDuration}s, max $MaxUsers" -ForegroundColor Cyan
Write-Host "  Stop: p95 > ${LatencyMult}x baseline OR error > $($ErrorThresh*100)%" -ForegroundColor Cyan
Write-Host "  Target: $Host" -ForegroundColor Cyan
Write-Host "================================================================" -ForegroundColor Cyan
Write-Host ""

# ── environment ────────────────────────────────────────────────────────────────
$env:STRESS_AUTH_TOKEN    = $Token
$env:TARGET_HOST          = $Host
$env:RAMP_STEP_USERS      = $StepUsers
$env:RAMP_STEP_DURATION   = $StepDuration
$env:RAMP_MAX_USERS       = $MaxUsers
$env:RAMP_LATENCY_MULT    = $LatencyMult
$env:RAMP_ERROR_THRESH    = $ErrorThresh

# ── sys_monitor ────────────────────────────────────────────────────────────────
Write-Host "[1/4] Starting sys_monitor..." -ForegroundColor Yellow
$MonArgs = @(
    (Join-Path $SharedDir "sys_monitor.py"),
    "--output", $ResultsDir,
    "--interval", $MonInterval,
    "--health-url", "$Host/health"
)
$MonProc = Start-Process python -ArgumentList $MonArgs -PassThru -NoNewWindow
Write-Host "      PID: $($MonProc.Id)"
Start-Sleep -Seconds 2

# ── locust ─────────────────────────────────────────────────────────────────────
Write-Host "[2/4] Running Locust (ramp shape active — will auto-stop)..." -ForegroundColor Yellow

# Estimate max runtime: all steps + extra buffer
$MaxRuntime = ($MaxUsers / $StepUsers) * $StepDuration + 120

$LocustArgs = @(
    "-f", $LocustFile,
    "--host", $Host,
    "--run-time", "${MaxRuntime}s",
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
python (Join-Path $SharedDir "post_process.py") --results $ResultsDir --bucket 30

# ── show breakpoint ────────────────────────────────────────────────────────────
$BpFile = Join-Path $ResultsDir "breakpoint.json"
if (Test-Path $BpFile) {
    $bp = Get-Content $BpFile | ConvertFrom-Json
    Write-Host ""
    Write-Host "================================================================" -ForegroundColor Green
    Write-Host "  BREAKPOINT DETECTED" -ForegroundColor Green
    Write-Host "  Users      : $($bp.user_count)" -ForegroundColor Green
    Write-Host "  p95 latency: $($bp.p95_ms) ms" -ForegroundColor Green
    Write-Host "  Error rate : $($bp.error_rate_pct)%" -ForegroundColor Green
    Write-Host "  Trigger    : $($bp.trigger_reason)" -ForegroundColor Green
    Write-Host "  -> Scenario 3 will use: $([math]::Floor($bp.user_count * 0.8)) users (80%)" -ForegroundColor Green
    Write-Host "================================================================" -ForegroundColor Green
}
