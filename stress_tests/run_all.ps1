#Requires -Version 5.1
<#
.SYNOPSIS
  Run all three stress scenarios in sequence.

.DESCRIPTION
  1. Scenario 1 (Baseline)   → establishes p95 floor
  2. Scenario 2 (Ramp)       → finds breaking point
  3. Scenario 3 (Saturation) → 30-min stability at 80% of breaking point

.EXAMPLE
  # Set token once and run everything:
  $env:STRESS_AUTH_TOKEN = "eyJhb..."
  .\run_all.ps1

  # With custom host and skip confirmation:
  .\run_all.ps1 -Host "http://myserver:8001" -Force
#>

param(
    [string]$Token       = $env:STRESS_AUTH_TOKEN,
    [string]$Host        = "http://localhost:8001",
    [switch]$Force,         # skip confirmation prompts
    [switch]$SkipS1,        # skip Scenario 1 (use existing baseline.json)
    [switch]$SkipS2,        # skip Scenario 2 (use existing breakpoint.json)
    [switch]$WebUI
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ScriptDir = $PSScriptRoot

function Confirm-Step([string]$name) {
    if ($Force) { return }
    $ans = Read-Host "`nReady to run $name ? [Y/n]"
    if ($ans -match "^[Nn]") { exit 0 }
}

Write-Host ""
Write-Host "############################################################" -ForegroundColor Magenta
Write-Host "#  CHATBOT STRESS TEST SUITE — FULL RUN                   #" -ForegroundColor Magenta
Write-Host "#  Host: $Host" -ForegroundColor Magenta
Write-Host "############################################################" -ForegroundColor Magenta
Write-Host ""

if (-not $Token) {
    Write-Error "STRESS_AUTH_TOKEN is not set. Export it before running:`n  `$env:STRESS_AUTH_TOKEN = `"<firebase_id_token>`""
}

$commonArgs = @("-Token", $Token, "-Host", $Host)
if ($WebUI) { $commonArgs += "-WebUI" }

$t0 = Get-Date

# ── Scenario 1 ─────────────────────────────────────────────────────────────────
if (-not $SkipS1) {
    Confirm-Step "Scenario 1 (Baseline - 5 min)"
    Write-Host "`n[$(Get-Date -Format 'HH:mm:ss')] Starting Scenario 1..." -ForegroundColor Cyan
    & (Join-Path $ScriptDir "run_scenario1.ps1") @commonArgs
    Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Scenario 1 complete." -ForegroundColor Green
} else {
    Write-Host "Skipping Scenario 1 (using existing baseline.json)" -ForegroundColor DarkGray
}

Start-Sleep -Seconds 10   # cooldown between scenarios

# ── Scenario 2 ─────────────────────────────────────────────────────────────────
if (-not $SkipS2) {
    Confirm-Step "Scenario 2 (Ramp - 20 min)"
    Write-Host "`n[$(Get-Date -Format 'HH:mm:ss')] Starting Scenario 2..." -ForegroundColor Cyan
    & (Join-Path $ScriptDir "run_scenario2.ps1") @commonArgs
    Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Scenario 2 complete." -ForegroundColor Green
} else {
    Write-Host "Skipping Scenario 2 (using existing breakpoint.json)" -ForegroundColor DarkGray
}

Start-Sleep -Seconds 30   # longer cooldown before saturation test

# ── Scenario 3 ─────────────────────────────────────────────────────────────────
Confirm-Step "Scenario 3 (Saturation - 30 min)"
Write-Host "`n[$(Get-Date -Format 'HH:mm:ss')] Starting Scenario 3..." -ForegroundColor Cyan
& (Join-Path $ScriptDir "run_scenario3.ps1") @commonArgs
Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Scenario 3 complete." -ForegroundColor Green

# ── final summary ──────────────────────────────────────────────────────────────
$elapsed = [math]::Round(((Get-Date) - $t0).TotalMinutes, 1)
Write-Host ""
Write-Host "############################################################" -ForegroundColor Magenta
Write-Host "#  ALL SCENARIOS COMPLETE  (${elapsed} min total)              #" -ForegroundColor Magenta
Write-Host "############################################################" -ForegroundColor Magenta
Write-Host ""
Write-Host "Results:"

foreach ($s in @("scenario_1_baseline","scenario_2_ramp","scenario_3_saturation")) {
    $dir = Join-Path $ScriptDir "results\$s"
    if (Test-Path $dir) {
        $csv = Join-Path $dir "consolidated.csv"
        $rows = if (Test-Path $csv) { (Import-Csv $csv).Count } else { 0 }
        Write-Host "  $s  ($rows rows)" -ForegroundColor White
    }
}

Write-Host ""
Write-Host "Key files:"
Write-Host "  results/scenario_1_baseline/baseline.json" -ForegroundColor DarkCyan
Write-Host "  results/scenario_2_ramp/breakpoint.json" -ForegroundColor DarkCyan
Write-Host "  results/scenario_3_saturation/rolling_stats.csv" -ForegroundColor DarkCyan
Write-Host "  */consolidated.csv - cross-scenario analysis" -ForegroundColor DarkCyan
