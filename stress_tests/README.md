# Stress Test Suite — chatbot-infraestructura

Three progressive Locust scenarios designed to attack each bottleneck
identified in `service_profile.json`.

---

## Prerequisites

```powershell
# From the stress_tests/ directory:
pip install -r requirements.txt

# Verify locust is available:
locust --version   # should print "locust 2.32.2"
```

---

## Auth token (required)

Every `/chat` request needs a valid Firebase ID token. Get one from the browser:

1. Open `http://localhost:3000` and log in
2. Open DevTools → Console, run:
   ```js
   firebase.auth().currentUser.getIdToken(true).then(t => console.log(t))
   ```
3. Copy the token and set it:
   ```powershell
   $env:STRESS_AUTH_TOKEN = "eyJhbGci..."
   ```

Tokens expire after **1 hour**. For long runs (Scenario 3 = 30 min) refresh
the token before starting. The test will continue with cached tokens on the
server side until they expire.

---

## Running the scenarios

### Option A — Run all three in sequence

```powershell
cd stress_tests\
$env:STRESS_AUTH_TOKEN = "eyJhbGci..."
.\run_all.ps1
```

The orchestrator pauses between scenarios for cooldown and asks for
confirmation before each step. Pass `-Force` to skip confirmation prompts.

### Option B — Run individually

```powershell
# Scenario 1 — Baseline (~5 min for 100 requests)
.\run_scenario1.ps1 -Token $env:STRESS_AUTH_TOKEN

# Scenario 2 — Ramp (auto-stops at breakpoint, up to ~20 min)
.\run_scenario2.ps1 -Token $env:STRESS_AUTH_TOKEN

# Scenario 3 — Saturation (30 min, reads breakpoint from S2)
.\run_scenario3.ps1 -Token $env:STRESS_AUTH_TOKEN
```

### Option C — Web UI (interactive)

```powershell
.\run_scenario2.ps1 -Token $env:STRESS_AUTH_TOKEN -WebUI
# Then open http://localhost:8089 to control the test interactively
```

---

## Scenario details

### Scenario 1 — Baseline

| Parameter | Value |
|---|---|
| Users | 1 (sequential) |
| Requests | 100 |
| Dataset | `test_payloads.jsonl` (all categories, in order) |
| Wait time | 0 (no pause) |
| Expected duration | ~5–15 min (depends on Mistral latency) |

**Goal:** Measure the floor latency with zero contention. No queue build-up,
no Firebase saturation, no concurrent Mistral calls.

**Output:** `baseline.json` with `p50_ms`, `p95_ms`, `p99_ms` — used by
Scenario 2 to calibrate the auto-stop threshold.

**When to re-run:** After any infrastructure change that could affect baseline
performance (new model, cache layer, reduced history size).

---

### Scenario 2 — Ramp

| Parameter | Value |
|---|---|
| Users | 1 → 100 (+5 every 60 s) |
| Dataset | `test_payloads.jsonl` (random) |
| Wait time | 0 |
| Max duration | ~20 min (stops earlier at breakpoint) |

**Auto-stop criteria** (whichever triggers first):
- `p95 > 3× baseline_p95`
- `error_rate > 5%`
- Max users (100) reached

**Output:** `breakpoint.json`:
```json
{
  "user_count": 25,
  "p95_ms": 18400,
  "baseline_p95_ms": 4200,
  "p95_ratio": 4.38,
  "error_rate_pct": 1.2,
  "trigger_reason": "latency_3x_baseline"
}
```

**Tuning via env vars:**
```powershell
$env:RAMP_STEP_USERS    = 5      # users added per step
$env:RAMP_STEP_DURATION = 60     # seconds per step
$env:RAMP_MAX_USERS     = 100
$env:RAMP_LATENCY_MULT  = 3.0    # latency multiplier threshold
$env:RAMP_ERROR_THRESH  = 0.05   # error rate threshold (fraction)
```

---

### Scenario 3 — Saturation

| Parameter | Value |
|---|---|
| Users | 80% of breakpoint (auto-read from `breakpoint.json`) |
| Duration | 30 minutes |
| Dataset | 70% `test_payloads.jsonl` + 30% `test_payloads_burst.jsonl` |
| Wait time | 0.5–2 s (realistic usage simulation) |

**Goal:** Verify the service is stable at a load level it can sustain.
Monitors latency drift over time to detect memory leaks or gradual degradation.

**Key output — `rolling_stats.csv`** (per-minute windows):
```
ts, elapsed_s, count, p50_ms, p95_ms, p99_ms, error_count
```

**Latency drift check:** After the test, `run_scenario3.ps1` prints:
```
  First window p95 : 4100 ms
  Last window p95  : 4350 ms
  Drift            : +6.1%   ← < 20% → healthy
```
A drift > 20% suggests memory accumulation or growing Firestore documents.

**Override user count:**
```powershell
.\run_scenario3.ps1 -SatUsers 15   # ignore breakpoint.json
```

---

## Output structure

```
stress_tests/
└── results/
    ├── scenario_1_baseline/
    │   ├── locust_stats.csv           # per-endpoint aggregate
    │   ├── locust_stats_history.csv   # time-series (Locust native)
    │   ├── locust_failures.csv        # failed requests
    │   ├── report.html                # Locust HTML report
    │   ├── sys_monitor.csv            # CPU/RAM/VRAM/service metrics
    │   ├── requests_detail.csv        # per-request: category, tokens_in, latency
    │   ├── baseline.json              # p50/p95/p99 floor → used by S2
    │   └── consolidated.csv           # joined: locust + sys + detail
    │
    ├── scenario_2_ramp/
    │   ├── ...
    │   ├── breakpoint.json            # user count at break → used by S3
    │   └── consolidated.csv
    │
    └── scenario_3_saturation/
        ├── ...
        ├── rolling_stats.csv          # per-minute latency drift data
        └── consolidated.csv
```

### `consolidated.csv` columns

| Column | Source | Description |
|---|---|---|
| `ts_epoch` / `ts_iso` | Locust | Timestamp |
| `user_count` | Locust | Concurrent users at this tick |
| `rps` / `fail_rps` | Locust | Requests & failures per second |
| `error_rate_pct` | Derived | `fail / total × 100` |
| `p50_ms` / `p95_ms` / `p99_ms` | Locust | Latency percentiles |
| `sys_cpu_avg` / `sys_ram_mb` | sys_monitor | Host system metrics |
| `sys_vram_mb` | sys_monitor | GPU VRAM (0 if no GPU) |
| `svc_ram_mb` / `svc_cpu_avg` | `/health` endpoint | Service-level metrics |
| `cat_simple/medium/complex/extreme` | requests_detail | Requests per category in bucket |
| `detail_avg_tokens` | requests_detail | Avg estimated input tokens |
| `p95_vs_baseline` | Derived | p95 / baseline_p95 ratio |
| `above_breakpoint` | Derived | True if p95 > 3× baseline |

---

## Correlating results with bottlenecks

From `service_profile.json`:

| Bottleneck ID | How to isolate in results |
|---|---|
| BN-01 Mistral latency | Filter `cat_complex` + `cat_extreme` rows in `requests_detail.csv`, compare `response_time_ms` vs `cat_simple` |
| BN-02 Firestore history growth | Rerun S1 with sessions that have 0, 50, 200 messages — compare `p95_ms` |
| BN-03 Tavily search overhead | `requests_detail.csv` shows response times per category; complex/extreme invoke Tavily more often |
| BN-04 Admin collection scan | Add `GET /admin/sessions` task to a locustfile and measure `p95_ms` vs session count |
| BN-05 Firebase token verify | Compare `GET /health` (no auth) vs `GET /history` (auth) latency in `locust_stats.csv` |

---

## Troubleshooting

**All requests fail with 401:**
Token expired or not set. Re-export `STRESS_AUTH_TOKEN`.

**All requests fail with 403:**
The test user doesn't have `assistant_user` role.
Run: `python backend/assign_role.py <uid> assistant_user`

**Locust exits immediately:**
Check that the backend is running: `curl http://localhost:8001/health`

**sys_monitor shows 0.0 for svc_ram_mb:**
The `/health` endpoint isn't reachable from sys_monitor or the service
hasn't received any requests yet (metrics start at 0).

**Scenario 3 crashes on startup:**
Run Scenario 2 first to generate `breakpoint.json`, or pass `-SatUsers <n>`.
