#Requires -Version 5.1
<#
.SYNOPSIS
  Scenario 3 — SATURATION: hold 80% of breakpoint users for 30 minutes.

.DESCRIPTION
  Reads breakpoint.json from Scenario 2 to set the target concurrency.
  Uses a mixed dataset: 70% normal + 30% burst payloads.
  Writes rolling_stats.csv (per-minute p95) to detect latency drift / memory leaks.
#>

param(
    [string]$Token        = $env:STRESS_AUTH_TOKEN,
    [string]$Host         = "http://localhost:8001",
    [int]   $DurationMin  = 30,
    [int]   $SatUsers     = 0,     # 0 = auto from breakpoint.json (80%)
    [float] $NormalWeight = 0.70,
    [float] $BurstWeight  = 0.30,
    [int]   $MonInterval  = 10,    # longer interval — 30 min test
    [switch]$WebUI
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ScriptDir  = $PSScriptRoot
$ResultsDir = Join-Path $ScriptDir "results\scenario_3_saturation"
$SharedDir  = Join-Path $ScriptDir "shared"
$LocustFile = Join-Path $ScriptDir "scenario_3_saturation\locustfile.py"

New-Item -ItemType Directory -Force -Path $ResultsDir | Out-Null

# Resolve user count
if ($SatUsers -eq 0) {
    $BpFile = Join-Path $ScriptDir "results\scenario_2_ramp\breakpoint.json"
    if (Test-Path $BpFile) {
        $bp       = Get-Content $BpFile | ConvertFrom-Json
        $SatUsers = [math]::Max(1, [math]::Floor($bp.user_count * 0.8))
        Write-Host "Breakpoint: $($bp.user_count) users -> Saturation target: $SatUsers users (80%)"
    } else {
        $SatUsers = 10
        Write-Warning "No breakpoint.json found. Defaulting to $SatUsers users."
        Write-Warning "Run run_scenario2.ps1 first, or pass -SatUsers <n>."
    }
}

if (-not $Token) {
    Write-Warning "No auth token. Requests will get HTTP 401."
}

$DurationS = $DurationMin * 60

Write-Host ""
Write-Host "================================================================" -ForegroundColor Cyan
Write-Host "  SCENARIO 3 — SATURATION (stability test)" -ForegroundColor Cyan
Write-Host "  Users   : $SatUsers (held constant for $DurationMin minutes)" -ForegroundColor Cyan
Write-Host "  Dataset : ${NormalWeight}x normal + ${BurstWeight}x burst" -ForegroundColor Cyan
Write-Host "  Target  : $Host" -ForegroundColor Cyan
Write-Host "================================================================" -ForegroundColor Cyan
Write-Host ""

# ── environment ────────────────────────────────────────────────────────────────
$env:STRESS_AUTH_TOKEN   = $Token
$env:TARGET_HOST         = $Host
$env:SAT_DURATION_S      = $DurationS
$env:SAT_USERS           = $SatUsers
$env:SAT_NORMAL_WEIGHT   = $NormalWeight
$env:SAT_BURST_WEIGHT    = $BurstWeight

# ── sys_monitor ────────────────────────────────────────────────────────────────
Write-Host "[1/4] Starting sys_monitor (interval=${MonInterval}s)..." -ForegroundColor Yellow
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
Write-Host "[2/4] Running Locust ($DurationMin minutes)..." -ForegroundColor Yellow
Write-Host "      Rolling stats written every 60s to rolling_stats.csv" -ForegroundColor DarkGray

$LocustArgs = @(
    "-f", $LocustFile,
    "--host", $Host,
    "--run-time", "${DurationS}s",
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
python (Join-Path $SharedDir "post_process.py") --results $ResultsDir --bucket 60

# ── latency drift analysis ─────────────────────────────────────────────────────
$RollingFile = Join-Path $ResultsDir "rolling_stats.csv"
if (Test-Path $RollingFile) {
    $rows = Import-Csv $RollingFile
    if ($rows.Count -ge 2) {
        $first_p95 = [double]$rows[0].p95_ms
        $last_p95  = [double]$rows[-1].p95_ms
        $drift     = if ($first_p95 -gt 0) { [math]::Round(($last_p95 - $first_p95) / $first_p95 * 100, 1) } else { 0 }
        Write-Host ""
        Write-Host "================================================================" -ForegroundColor $(if ([math]::Abs($drift) -gt 20) { "Red" } else { "Green" })
        Write-Host "  LATENCY DRIFT ANALYSIS" -ForegroundColor White
        Write-Host "  First window p95 : $($first_p95) ms" -ForegroundColor White
        Write-Host "  Last window p95  : $($last_p95) ms" -ForegroundColor White
        Write-Host "  Drift            : $drift%" -ForegroundColor $(if ([math]::Abs($drift) -gt 20) { "Red" } else { "Green" })
        if ([math]::Abs($drift) -gt 20) {
            Write-Host "  WARNING: Significant latency drift detected (>20%)." -ForegroundColor Red
            Write-Host "           Possible memory leak or resource accumulation." -ForegroundColor Red
        } else {
            Write-Host "  Latency is stable (drift < 20%). Service appears healthy." -ForegroundColor Green
        }
        Write-Host "================================================================" -ForegroundColor White
    }
}

Write-Host ""
Write-Host "All results in: $ResultsDir" -ForegroundColor Green
