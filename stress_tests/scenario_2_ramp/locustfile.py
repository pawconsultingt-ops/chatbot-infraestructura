"""
SCENARIO 2 — RAMP  (find the breaking point)
=============================================
Shape  : 1 → 100 concurrent users, +5 every 60 s
Dataset: test_payloads.jsonl

Auto-stop triggers (whichever comes first):
  - p95 latency  > 3× the baseline p95 from scenario_1_baseline/baseline.json
  - Error rate   > 5%
  - Max users    reached (100)

On stop, writes results/scenario_2_ramp/breakpoint.json with:
  user_count, p95_ms, error_rate_pct, trigger_reason

Run via: run_scenario2.ps1
"""

from __future__ import annotations

import csv
import json
import os
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

from locust import HttpUser, LoadTestShape, constant, events, task

_HERE    = Path(__file__).parent
_ROOT    = _HERE.parent.parent
_RESULTS = _HERE.parent / "results" / "scenario_2_ramp"

sys.path.insert(0, str(_HERE.parent))
from shared.payload_loader import PayloadLoader

# ── config ─────────────────────────────────────────────────────────────────────
DATASET     = _ROOT / "test_payloads.jsonl"
AUTH_TOKEN  = os.getenv("STRESS_AUTH_TOKEN", "")
TARGET_HOST = os.getenv("TARGET_HOST", "http://localhost:8001")

STEP_USERS    = int(os.getenv("RAMP_STEP_USERS",    "5"))
STEP_DURATION = int(os.getenv("RAMP_STEP_DURATION", "60"))   # seconds per step
MAX_USERS     = int(os.getenv("RAMP_MAX_USERS",     "100"))
LATENCY_MULT  = float(os.getenv("RAMP_LATENCY_MULT",  "3.0"))
ERROR_THRESH  = float(os.getenv("RAMP_ERROR_THRESH",   "0.05"))
WARMUP_S      = int(os.getenv("RAMP_WARMUP_S",        "90"))  # ignore stop criteria during warmup
MIN_SAMPLES   = int(os.getenv("RAMP_MIN_SAMPLES",     "20"))  # need at least N requests before checking

_RESULTS.mkdir(parents=True, exist_ok=True)

# ── baseline p95 (from scenario 1) ────────────────────────────────────────────
_baseline_file = _HERE.parent / "results" / "scenario_1_baseline" / "baseline.json"
_BASELINE_P95: float = 0.0

if _baseline_file.exists():
    try:
        _BASELINE_P95 = json.loads(_baseline_file.read_text())["p95_ms"]
        print(f"[ramp] Loaded baseline p95: {_BASELINE_P95:.0f} ms  "
              f"(stop threshold: {_BASELINE_P95 * LATENCY_MULT:.0f} ms)")
    except Exception as e:
        print(f"[ramp] Could not read baseline: {e} — latency stop criterion disabled")
else:
    print("[ramp] No baseline.json found — latency stop criterion disabled")

# ── shared state ───────────────────────────────────────────────────────────────
_loader      = PayloadLoader(DATASET)
_bp_saved    = threading.Event()
_detail_lock = threading.Lock()
_detail_fh   = None
_detail_writer = None


def _init_detail_csv() -> None:
    global _detail_fh, _detail_writer
    path = _RESULTS / "requests_detail.csv"
    _detail_fh = path.open("w", newline="", encoding="utf-8")
    _detail_writer = csv.DictWriter(
        _detail_fh,
        fieldnames=["ts", "category", "tokens_in", "response_time_ms", "success", "error"],
    )
    _detail_writer.writeheader()
    _detail_fh.flush()


def _write_detail(category: str, tokens_in: int, resp_ms: float,
                  success: bool, error: str = "") -> None:
    with _detail_lock:
        if _detail_writer:
            _detail_writer.writerow({
                "ts":               datetime.now(timezone.utc).isoformat(),
                "category":         category,
                "tokens_in":        tokens_in,
                "response_time_ms": round(resp_ms, 2),
                "success":          success,
                "error":            error,
            })
            _detail_fh.flush()  # type: ignore[union-attr]


def _save_breakpoint(user_count: int, p95: float, error_rate: float,
                     reason: str) -> None:
    if _bp_saved.is_set():
        return
    _bp_saved.set()
    data = {
        "user_count":      user_count,
        "p95_ms":          round(p95, 2),
        "baseline_p95_ms": _BASELINE_P95,
        "p95_ratio":       round(p95 / _BASELINE_P95, 2) if _BASELINE_P95 else None,
        "error_rate_pct":  round(error_rate * 100, 2),
        "trigger_reason":  reason,
        "detected_at":     datetime.now(timezone.utc).isoformat(),
    }
    out = _RESULTS / "breakpoint.json"
    out.write_text(json.dumps(data, indent=2))
    print(f"\n[ramp] BREAKPOINT at {user_count} users  p95={p95:.0f}ms  "
          f"errors={error_rate*100:.1f}%  reason={reason}")
    print(f"[ramp] Saved: {out}")


# ── load shape ─────────────────────────────────────────────────────────────────

class BreakpointRampShape(LoadTestShape):
    """
    Steps up 5 users every 60 s up to 100.
    Checks stop criteria every tick (≈1 s) after the warmup period.
    Returns None (stop) as soon as a criterion is met.
    """

    _last_check_time: float = 0.0
    _check_interval: float  = 10.0   # seconds between criteria evaluations

    def tick(self):
        run_time = self.get_run_time()

        # Compute target users for this step
        step         = int(run_time / STEP_DURATION)
        target_users = min((step + 1) * STEP_USERS, MAX_USERS)

        # Skip stop criteria during warmup or with too few samples
        if run_time < WARMUP_S:
            return (target_users, STEP_USERS)

        # Rate-limit the criteria check to avoid excessive computation
        if run_time - self._last_check_time < self._check_interval:
            return (target_users, STEP_USERS)
        self._last_check_time = run_time

        stats = self.runner.stats.total
        if stats.num_requests < MIN_SAMPLES:
            return (target_users, STEP_USERS)

        p95        = stats.get_response_time_percentile(0.95) or 0
        error_rate = stats.fail_ratio   # fraction [0,1]

        # Criterion 1: latency > 3× baseline
        if _BASELINE_P95 and p95 > LATENCY_MULT * _BASELINE_P95:
            _save_breakpoint(target_users, p95, error_rate, "latency_3x_baseline")
            return None   # stop the test

        # Criterion 2: error rate > 5%
        if error_rate > ERROR_THRESH:
            _save_breakpoint(target_users, p95, error_rate, "error_rate_5pct")
            return None

        # Criterion 3: reached max users — note it but keep running briefly
        if target_users >= MAX_USERS and run_time > WARMUP_S + STEP_DURATION:
            _save_breakpoint(target_users, p95, error_rate, "max_users_reached")
            return None

        return (target_users, STEP_USERS)


# ── locust events ──────────────────────────────────────────────────────────────

@events.init.add_listener
def on_init(environment, **_):
    _init_detail_csv()
    print(f"[ramp] Dataset: {DATASET}  ({_loader.total} payloads)")
    print(f"[ramp] Shape: +{STEP_USERS} users every {STEP_DURATION}s, max {MAX_USERS}")
    if not AUTH_TOKEN:
        print("[ramp] WARNING: STRESS_AUTH_TOKEN not set — requests will get 401")


@events.quitting.add_listener
def on_quitting(environment, **_):
    if _detail_fh:
        _detail_fh.flush()
        _detail_fh.close()
    # Save breakpoint if not already saved (e.g. test ended at max users without triggering)
    if not _bp_saved.is_set():
        stats = environment.runner.stats.total
        p95   = stats.get_response_time_percentile(0.95) or 0
        _save_breakpoint(
            environment.runner.user_count,
            p95,
            stats.fail_ratio,
            "test_ended_normally",
        )


# ── user ───────────────────────────────────────────────────────────────────────

class RampUser(HttpUser):
    host      = TARGET_HOST
    wait_time = constant(0)

    network_timeout    = 120.0
    connection_timeout = 30.0

    def on_start(self) -> None:
        self.headers = {"Authorization": f"Bearer {AUTH_TOKEN}"}
        # Each user gets its own random seed offset for payload selection
        import random
        self._user_rng = random.Random()

    @task
    def chat(self) -> None:
        record  = _loader.pick_random()
        payload = _loader.to_chat_payload(record, user_suffix=str(id(self) % 9999))

        with self.client.post(
            "/chat",
            json=payload,
            headers=self.headers,
            catch_response=True,
            name=f"/chat [{record['expected_category']}]",
            timeout=120,
        ) as resp:
            if resp.status_code == 200:
                resp.success()
                _write_detail(
                    record["expected_category"],
                    record["estimated_tokens_in"],
                    resp.elapsed.total_seconds() * 1000,
                    True,
                )
            else:
                msg = f"HTTP {resp.status_code}"
                resp.failure(msg)
                _write_detail(
                    record["expected_category"],
                    record["estimated_tokens_in"],
                    resp.elapsed.total_seconds() * 1000,
                    False,
                    msg,
                )
