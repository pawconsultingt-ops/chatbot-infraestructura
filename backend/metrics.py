"""
Request metrics collection.

Architecture:
  - Hot path (middleware): two perf_counter() calls + 3 short lock acquisitions
    + one non-blocking queue.put_nowait(). Typical overhead: 0.05-0.15 ms.
  - CSV writer: dedicated daemon thread that drains the queue and rotates files
    hourly.  All disk I/O is off the request path.
  - System poller: daemon thread that samples RAM/CPU/VRAM every 5 s and stores
    the snapshot behind a lock; middleware stamps it onto each record at ~0 cost.
  - ContextVar: lets the middleware and route layers share sub-request data
    (inference_ms, token counts, uid) without coupling them directly.
"""

from __future__ import annotations

import csv
import queue
import threading
import time
from collections import deque
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from dataclasses import fields as _dc_fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psutil

# ── optional GPU (nvidia) support ─────────────────────────────────────────────
try:
    import pynvml  # type: ignore
    pynvml.nvmlInit()
    _GPU = True
except Exception:
    _GPU = False

# ── tunables ───────────────────────────────────────────────────────────────────
METRICS_DIR = Path("./metrics")
_RING_SIZE = 1_000    # requests retained for percentile computation
_SYS_POLL_S = 5.0    # system snapshot interval (seconds)
_RPS_WINDOW_S = 60.0  # sliding window for throughput (seconds)
_FLUSH_EVERY = 50     # CSV rows between flushes


# ── per-request context ────────────────────────────────────────────────────────
# Set to {} by the metrics middleware at request start.
# Route layers write into this dict; middleware reads it after call_next.
# Default None lets callers detect "outside a request context".
request_ctx: ContextVar[dict[str, Any] | None] = ContextVar(
    "request_ctx", default=None
)


# ── data structures ────────────────────────────────────────────────────────────

@dataclass
class RequestRecord:
    ts: str             # ISO-8601 UTC
    endpoint: str
    method: str
    status_code: int
    latency_e2e_ms: float
    inference_ms: float  # 0 for non-LLM endpoints
    tokens_in: int
    tokens_out: int
    used_search: bool
    req_bytes: int
    resp_bytes: int
    uid: str
    ram_used_mb: float   # stamped from latest system snapshot
    cpu_avg_pct: float   # stamped from latest system snapshot


@dataclass
class _SysSnap:
    ram_used_mb: float = 0.0
    ram_total_mb: float = 0.0
    ram_pct: float = 0.0
    cpu_cores: list[float] = field(default_factory=list)
    cpu_avg: float = 0.0
    vram_used_mb: float = 0.0
    vram_total_mb: float = 0.0
    collected_at: str = ""


# ── collector ──────────────────────────────────────────────────────────────────

class MetricsCollector:
    """Thread-safe metrics hub. All public methods safe to call from any thread."""

    # CSV column order derived from the dataclass field order at import time.
    _CSV_FIELDS: list[str] = [f.name for f in _dc_fields(RequestRecord)]

    def __init__(self) -> None:
        # Ring buffer for latency percentiles
        self._ring: deque[float] = deque(maxlen=_RING_SIZE)
        self._ring_lock = threading.Lock()

        # Monotonic timestamps for throughput (req/s)
        self._req_times: deque[float] = deque()
        self._times_lock = threading.Lock()

        # Simple counters
        self._total = 0
        self._errors = 0
        self._cnt_lock = threading.Lock()

        # Latest system snapshot
        self._sys = _SysSnap()
        self._sys_lock = threading.Lock()

        # Non-blocking write queue; shed load rather than block requests
        self._q: queue.Queue[RequestRecord] = queue.Queue(maxsize=10_000)

        METRICS_DIR.mkdir(parents=True, exist_ok=True)

        threading.Thread(
            target=self._csv_writer, daemon=True, name="metrics-csv"
        ).start()
        threading.Thread(
            target=self._sys_poller, daemon=True, name="metrics-sys"
        ).start()

    # ── hot path ───────────────────────────────────────────────────────────────

    def record(self, rec: RequestRecord) -> None:
        """Stamp system metrics onto rec and enqueue for CSV writing.

        Designed for < 0.2 ms overhead: three short critical sections and one
        non-blocking queue.put_nowait().
        """
        now = time.monotonic()

        with self._ring_lock:
            self._ring.append(rec.latency_e2e_ms)

        with self._times_lock:
            self._req_times.append(now)
            cutoff = now - _RPS_WINDOW_S
            while self._req_times and self._req_times[0] < cutoff:
                self._req_times.popleft()

        with self._cnt_lock:
            self._total += 1
            if rec.status_code >= 500:
                self._errors += 1

        with self._sys_lock:
            rec.ram_used_mb = self._sys.ram_used_mb
            rec.cpu_avg_pct = self._sys.cpu_avg

        try:
            self._q.put_nowait(rec)
        except queue.Full:
            pass  # never block the request; metric loss is acceptable

    # ── snapshot for /health ───────────────────────────────────────────────────

    def snapshot(self) -> dict[str, Any]:
        """Compute a real-time metrics snapshot. Not for the hot path."""
        with self._ring_lock:
            samples = sorted(self._ring)
        n = len(samples)

        def _pct(p: float) -> float:
            if n == 0:
                return 0.0
            if n == 1:
                return round(samples[0], 2)
            rank = (p / 100.0) * (n - 1)
            lo, hi = int(rank), min(int(rank) + 1, n - 1)
            return round(samples[lo] + (rank - lo) * (samples[hi] - samples[lo]), 2)

        with self._times_lock:
            rps = round(len(self._req_times) / _RPS_WINDOW_S, 3)

        with self._cnt_lock:
            total, errors = self._total, self._errors

        with self._sys_lock:
            s = self._sys

        return {
            "latency_ms": {
                "p50": _pct(50),
                "p95": _pct(95),
                "p99": _pct(99),
                "sample_count": n,
                "window": f"last {_RING_SIZE} requests",
            },
            "throughput": {
                "rps_last_60s": rps,
                "total_requests": total,
                "total_errors_5xx": errors,
                "error_rate_pct": round(errors / total * 100, 2) if total else 0.0,
            },
            "system": {
                "ram_used_mb": s.ram_used_mb,
                "ram_total_mb": s.ram_total_mb,
                "ram_used_pct": s.ram_pct,
                "cpu_per_core_pct": s.cpu_cores,
                "cpu_avg_pct": s.cpu_avg,
                "vram_used_mb": s.vram_used_mb,
                "vram_total_mb": s.vram_total_mb,
                "gpu_available": _GPU,
                "collected_at": s.collected_at,
            },
        }

    # ── background: CSV writer ─────────────────────────────────────────────────

    def _csv_writer(self) -> None:
        cur_hour = ""
        fh = None
        writer: csv.DictWriter | None = None
        unflushed = 0

        while True:
            try:
                rec = self._q.get(timeout=1.0)
            except queue.Empty:
                if fh:
                    fh.flush()
                    unflushed = 0
                continue

            # Derive the hour-bucket key from the ISO timestamp.
            # ts[:13] == "2026-04-30T14" — no colons, safe for Windows filenames.
            hour = rec.ts[:13]
            if hour != cur_hour:
                if fh:
                    fh.flush()
                    fh.close()
                cur_hour = hour
                path = METRICS_DIR / f"metrics_{hour}.csv"
                is_new = not path.exists()
                fh = open(path, "a", newline="", encoding="utf-8")
                writer = csv.DictWriter(fh, fieldnames=self._CSV_FIELDS)
                if is_new:
                    writer.writeheader()
                    fh.flush()
                unflushed = 0

            if writer and fh:
                writer.writerow(asdict(rec))
                unflushed += 1
                if unflushed >= _FLUSH_EVERY:
                    fh.flush()
                    unflushed = 0

    # ── background: system poller ──────────────────────────────────────────────

    def _sys_poller(self) -> None:
        while True:
            try:
                mem = psutil.virtual_memory()
                cores: list[float] = psutil.cpu_percent(percpu=True)  # type: ignore[assignment]

                vu = vt = 0.0
                if _GPU:
                    try:
                        h = pynvml.nvmlDeviceGetHandleByIndex(0)
                        info = pynvml.nvmlDeviceGetMemoryInfo(h)
                        vu = info.used / 1_048_576
                        vt = info.total / 1_048_576
                    except Exception:
                        pass

                snap = _SysSnap(
                    ram_used_mb=round(mem.used / 1_048_576, 1),
                    ram_total_mb=round(mem.total / 1_048_576, 1),
                    ram_pct=round(mem.percent, 1),
                    cpu_cores=[round(c, 1) for c in cores],
                    cpu_avg=round(sum(cores) / len(cores) if cores else 0.0, 1),
                    vram_used_mb=round(vu, 1),
                    vram_total_mb=round(vt, 1),
                    collected_at=datetime.now(timezone.utc).isoformat(),
                )
                with self._sys_lock:
                    self._sys = snap
            except Exception:
                pass
            time.sleep(_SYS_POLL_S)


# ── module-level singleton ─────────────────────────────────────────────────────
collector = MetricsCollector()
