"""
SCENARIO 1 — BASELINE
=====================
Goal   : establish the floor latency and minimum throughput with zero contention.
Users  : 1 (sequential, no concurrency)
Requests: 100 total, then stop automatically.
Dataset: test_payloads.jsonl  (all categories, original order)

Run via:  run_scenario1.ps1  (sets env vars and calls locust --headless)

Output written to results/scenario_1_baseline/:
  locust_stats.csv, locust_stats_history.csv, locust_failures.csv
  requests_detail.csv   (per-request: category, tokens_in, response_time)
  baseline.json         (p95 and p50 — read by Scenario 2 for stop criteria)
"""

from __future__ import annotations

import csv
import json
import os
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

from locust import HttpUser, between, constant, events, task
from locust.runners import MasterRunner, WorkerRunner

# ── path setup ─────────────────────────────────────────────────────────────────
_HERE    = Path(__file__).parent
_ROOT    = _HERE.parent.parent
_SHARED  = _HERE.parent / "shared"
_RESULTS = _HERE.parent / "results" / "scenario_1_baseline"

sys.path.insert(0, str(_HERE.parent))
from shared.payload_loader import PayloadLoader

# ── config ─────────────────────────────────────────────────────────────────────
DATASET        = _ROOT / "test_payloads.jsonl"
MAX_ITERATIONS = int(os.getenv("BASELINE_ITERATIONS", "100"))
AUTH_TOKEN     = os.getenv("STRESS_AUTH_TOKEN", "")
TARGET_HOST    = os.getenv("TARGET_HOST", "http://localhost:8001")

_RESULTS.mkdir(parents=True, exist_ok=True)

# ── shared state ───────────────────────────────────────────────────────────────
_loader = PayloadLoader(DATASET)
_done   = threading.Event()

# Thread-safe detail CSV writer
_detail_lock   = threading.Lock()
_detail_fh     = None
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


def _save_baseline(stats) -> None:
    p95 = stats.get_response_time_percentile(0.95) or 0
    p50 = stats.get_response_time_percentile(0.50) or 0
    p99 = stats.get_response_time_percentile(0.99) or 0
    data = {
        "p50_ms": p50,
        "p95_ms": p95,
        "p99_ms": p99,
        "total_requests": stats.num_requests,
        "total_failures": stats.num_failures,
        "rps": round(stats.current_rps, 3),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    out = _RESULTS / "baseline.json"
    out.write_text(json.dumps(data, indent=2))
    print(f"\n[baseline] Saved: {out}")
    print(f"[baseline] p50={p50:.0f}ms  p95={p95:.0f}ms  p99={p99:.0f}ms")


# ── locust events ──────────────────────────────────────────────────────────────

@events.init.add_listener
def on_init(environment, **_):
    _init_detail_csv()
    print(f"[scenario_1] Dataset: {DATASET}  ({_loader.total} payloads)")
    print(f"[scenario_1] Max iterations: {MAX_ITERATIONS}")
    if not AUTH_TOKEN:
        print("[scenario_1] WARNING: STRESS_AUTH_TOKEN not set — requests will get 401")


@events.quitting.add_listener
def on_quitting(environment, **_):
    if _detail_fh:
        _detail_fh.flush()
        _detail_fh.close()
    _save_baseline(environment.runner.stats.total)


# ── user ───────────────────────────────────────────────────────────────────────

class BaselineUser(HttpUser):
    host      = TARGET_HOST
    wait_time = constant(0)   # sequential — no pause between requests

    # Connection timeouts generous enough for slow LLM responses
    network_timeout    = 120.0
    connection_timeout = 30.0

    def on_start(self) -> None:
        self.headers = {"Authorization": f"Bearer {AUTH_TOKEN}"}
        self._iteration = 0

    @task
    def chat(self) -> None:
        record = _loader.next_sequential()
        if record is None or self._iteration >= MAX_ITERATIONS:
            # All payloads exhausted — signal locust to stop
            if not _done.is_set():
                _done.set()
                self.environment.runner.quit()
            return

        self._iteration += 1
        payload = _loader.to_chat_payload(record, user_suffix="baseline")

        with self.client.post(
            "/chat",
            json=payload,
            headers=self.headers,
            catch_response=True,
            name=f"/chat [{record['expected_category']}]",
            timeout=120,
            context={"category": record["expected_category"],
                     "tokens_in": record["estimated_tokens_in"]},
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

        # Print progress every 10 requests
        if self._iteration % 10 == 0:
            print(f"[baseline] Progress: {self._iteration}/{MAX_ITERATIONS}", flush=True)
