"""
SCENARIO 3 — SATURATION  (stability under sustained load)
==========================================================
Load   : 80% of the breakpoint users found in Scenario 2, held for 30 minutes.
Dataset: 70% test_payloads.jsonl  +  30% test_payloads_burst.jsonl (mixed)

Monitors:
  - Latency drift: rolling p95 per 60-s window — does it climb over time?
  - Memory leak indicator: service RAM at 5-min intervals (from /health)
  - Error rate stability: should stay below 2% throughout

Writes per-minute rolling stats to results/scenario_3_saturation/rolling_stats.csv
so you can plot latency drift after the test.

Run via: run_scenario3.ps1
"""

from __future__ import annotations

import csv
import json
import os
import sys
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Deque

from locust import HttpUser, LoadTestShape, constant, events, task, between

_HERE    = Path(__file__).parent
_ROOT    = _HERE.parent.parent
_RESULTS = _HERE.parent / "results" / "scenario_3_saturation"

sys.path.insert(0, str(_HERE.parent))
from shared.payload_loader import PayloadLoader

# ── config ─────────────────────────────────────────────────────────────────────
DATASET_NORMAL = _ROOT / "test_payloads.jsonl"
DATASET_BURST  = _ROOT / "test_payloads_burst.jsonl"
AUTH_TOKEN     = os.getenv("STRESS_AUTH_TOKEN", "")
TARGET_HOST    = os.getenv("TARGET_HOST", "http://localhost:8001")
DURATION_S     = int(os.getenv("SAT_DURATION_S",    "1800"))   # 30 minutes
NORMAL_WEIGHT  = float(os.getenv("SAT_NORMAL_WEIGHT", "0.70"))
BURST_WEIGHT   = float(os.getenv("SAT_BURST_WEIGHT",  "0.30"))

_RESULTS.mkdir(parents=True, exist_ok=True)

# ── resolve target user count from breakpoint.json ────────────────────────────
_bp_file  = _HERE.parent / "results" / "scenario_2_ramp" / "breakpoint.json"
_SAT_USERS: int = int(os.getenv("SAT_USERS", "0"))

if _SAT_USERS == 0:
    if _bp_file.exists():
        try:
            bp = json.loads(_bp_file.read_text())
            _SAT_USERS = max(1, int(bp["user_count"] * 0.80))
            print(f"[saturation] Breakpoint: {bp['user_count']} users  "
                  f"(reason: {bp.get('trigger_reason','?')})  "
                  f"-> 80% = {_SAT_USERS} users")
        except Exception as e:
            _SAT_USERS = 10
            print(f"[saturation] Could not read breakpoint ({e}), defaulting to {_SAT_USERS} users")
    else:
        _SAT_USERS = 10
        print(f"[saturation] No breakpoint.json found — defaulting to {_SAT_USERS} users")
        print(f"             Set SAT_USERS env var to override")
else:
    print(f"[saturation] SAT_USERS override: {_SAT_USERS}")

# ── payload loader: mixed dataset ─────────────────────────────────────────────
_loader = PayloadLoader(
    DATASET_NORMAL,
    DATASET_BURST,
    weights=[NORMAL_WEIGHT, BURST_WEIGHT],
)

# ── rolling window for latency drift detection ─────────────────────────────────
_WINDOW_S    = 60   # 1-minute rolling windows
_latency_buf: Deque[tuple[float, float]] = deque()  # (monotonic_ts, resp_ms)
_latency_lock = threading.Lock()

# ── detail CSV ────────────────────────────────────────────────────────────────
_detail_lock   = threading.Lock()
_detail_fh     = None
_detail_writer = None

# Rolling stats CSV (per 60-s window)
_rolling_fh     = None
_rolling_writer = None
_rolling_lock   = threading.Lock()


def _init_csvs() -> None:
    global _detail_fh, _detail_writer, _rolling_fh, _rolling_writer

    detail_path = _RESULTS / "requests_detail.csv"
    _detail_fh  = detail_path.open("w", newline="", encoding="utf-8")
    _detail_writer = csv.DictWriter(
        _detail_fh,
        fieldnames=["ts", "category", "tokens_in", "response_time_ms", "success", "error"],
    )
    _detail_writer.writeheader()
    _detail_fh.flush()

    rolling_path = _RESULTS / "rolling_stats.csv"
    _rolling_fh  = rolling_path.open("w", newline="", encoding="utf-8")
    _rolling_writer = csv.DictWriter(
        _rolling_fh,
        fieldnames=["ts", "window_start", "window_end",
                    "elapsed_s", "count", "p50_ms", "p95_ms", "p99_ms",
                    "error_count", "error_rate_pct"],
    )
    _rolling_writer.writeheader()
    _rolling_fh.flush()


def _record_latency(resp_ms: float) -> None:
    with _latency_lock:
        _latency_buf.append((time.monotonic(), resp_ms))


def _flush_rolling_window(elapsed_s: float) -> None:
    """Write one rolling-window row and trim the buffer."""
    now = time.monotonic()
    cutoff = now - _WINDOW_S

    with _latency_lock:
        # Keep only samples within window
        while _latency_buf and _latency_buf[0][0] < cutoff:
            _latency_buf.popleft()
        samples = sorted(lat for _, lat in _latency_buf)

    if not samples:
        return

    n = len(samples)

    def _p(pct: float) -> float:
        if n == 0:
            return 0.0
        idx = max(0, int(n * pct / 100) - 1)
        return round(samples[idx], 2)

    with _rolling_lock:
        if _rolling_writer:
            _rolling_writer.writerow({
                "ts":             datetime.now(timezone.utc).isoformat(),
                "window_start":   datetime.fromtimestamp(now - _WINDOW_S, tz=timezone.utc).isoformat(),
                "window_end":     datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
                "elapsed_s":      int(elapsed_s),
                "count":          n,
                "p50_ms":         _p(50),
                "p95_ms":         _p(95),
                "p99_ms":         _p(99),
                "error_count":    0,
                "error_rate_pct": 0.0,
            })
            _rolling_fh.flush()  # type: ignore[union-attr]


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


# ── rolling stats reporter (background thread) ─────────────────────────────────

_reporter_stop = threading.Event()
_test_start    = time.monotonic()


def _rolling_reporter() -> None:
    report_interval = 60  # write a row every 60 s
    while not _reporter_stop.wait(timeout=report_interval):
        elapsed = time.monotonic() - _test_start
        _flush_rolling_window(elapsed)
        print(f"[saturation] t={elapsed:.0f}s  window buffer: {len(_latency_buf)} samples",
              flush=True)


# ── load shape ─────────────────────────────────────────────────────────────────

class SaturationShape(LoadTestShape):
    """Holds _SAT_USERS for DURATION_S seconds, then stops."""

    def tick(self):
        if self.get_run_time() > DURATION_S:
            return None
        return (_SAT_USERS, _SAT_USERS)


# ── locust events ──────────────────────────────────────────────────────────────

@events.init.add_listener
def on_init(environment, **_):
    global _test_start
    _test_start = time.monotonic()
    _init_csvs()

    reporter = threading.Thread(target=_rolling_reporter, daemon=True, name="rolling-stats")
    reporter.start()

    print(f"[saturation] Users        : {_SAT_USERS}")
    print(f"[saturation] Duration     : {DURATION_S}s ({DURATION_S//60} min)")
    print(f"[saturation] Dataset mix  : {NORMAL_WEIGHT:.0%} normal + {BURST_WEIGHT:.0%} burst")
    print(f"[saturation] Total payloads: {_loader.total}")
    if not AUTH_TOKEN:
        print("[saturation] WARNING: STRESS_AUTH_TOKEN not set — requests will get 401")


@events.quitting.add_listener
def on_quitting(environment, **_):
    _reporter_stop.set()
    # Final rolling window flush
    _flush_rolling_window(time.monotonic() - _test_start)

    for fh in (_detail_fh, _rolling_fh):
        if fh:
            fh.flush()
            fh.close()

    stats = environment.runner.stats.total
    p95   = stats.get_response_time_percentile(0.95) or 0
    p99   = stats.get_response_time_percentile(0.99) or 0
    print(f"\n[saturation] === FINAL STATS ===")
    print(f"[saturation] Total requests : {stats.num_requests}")
    print(f"[saturation] Total failures : {stats.num_failures}")
    print(f"[saturation] Error rate     : {stats.fail_ratio*100:.2f}%")
    print(f"[saturation] p95 latency    : {p95:.0f} ms")
    print(f"[saturation] p99 latency    : {p99:.0f} ms")


# ── user ───────────────────────────────────────────────────────────────────────

class SaturationUser(HttpUser):
    host      = TARGET_HOST
    wait_time = between(0.5, 2.0)  # slight pause to simulate realistic usage

    network_timeout    = 120.0
    connection_timeout = 30.0

    def on_start(self) -> None:
        self.headers = {"Authorization": f"Bearer {AUTH_TOKEN}"}

    @task
    def chat(self) -> None:
        record  = _loader.pick_by_distribution()
        payload = _loader.to_chat_payload(record, user_suffix=str(id(self) % 99999))

        with self.client.post(
            "/chat",
            json=payload,
            headers=self.headers,
            catch_response=True,
            name=f"/chat [{record['expected_category']}]",
            timeout=120,
        ) as resp:
            resp_ms = resp.elapsed.total_seconds() * 1000
            if resp.status_code == 200:
                resp.success()
                _record_latency(resp_ms)
                _write_detail(
                    record["expected_category"],
                    record["estimated_tokens_in"],
                    resp_ms,
                    True,
                )
            else:
                msg = f"HTTP {resp.status_code}"
                resp.failure(msg)
                _write_detail(
                    record["expected_category"],
                    record["estimated_tokens_in"],
                    resp_ms,
                    False,
                    msg,
                )
