"""
sys_monitor.py  —  stand-alone system metrics collector

Runs in parallel with Locust (started by the run scripts as a background
process). Polls psutil + optional NVIDIA GPU + the service /health endpoint
every --interval seconds and writes rows to <output>/sys_monitor.csv.

Stops gracefully when:
  - SIGINT / SIGTERM is received, OR
  - a file named STOP_MONITOR appears in the output directory.

Usage:
    python shared/sys_monitor.py --output results/scenario_1 --interval 5
"""

from __future__ import annotations

import argparse
import csv
import json
import signal
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import psutil

try:
    import pynvml  # type: ignore
    pynvml.nvmlInit()
    _GPU = True
except Exception:
    _GPU = False

_STOP = False


def _sig(sig, frame):          # noqa: ANN001
    global _STOP
    _STOP = True


signal.signal(signal.SIGINT, _sig)
signal.signal(signal.SIGTERM, _sig)

FIELDS = [
    "ts",
    "cpu_avg_pct",
    "cpu_per_core_json",
    "ram_used_mb",
    "ram_total_mb",
    "ram_pct",
    "vram_used_mb",
    "vram_total_mb",
    "service_ram_mb",   # from GET /health
    "service_cpu_avg",  # from GET /health
]


def _health(url: str) -> tuple[float, float]:
    try:
        with urllib.request.urlopen(url, timeout=3) as r:
            data = json.loads(r.read())
            sys_data = data.get("metrics", {}).get("system", {})
            return float(sys_data.get("ram_used_mb", 0)), float(sys_data.get("cpu_avg_pct", 0))
    except Exception:
        return 0.0, 0.0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--output",     required=True,  help="Results directory")
    ap.add_argument("--interval",   type=float,     default=5.0)
    ap.add_argument("--health-url", default="http://localhost:8001/health")
    args = ap.parse_args()

    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    sentinel = out / "STOP_MONITOR"
    sentinel.unlink(missing_ok=True)

    csv_path = out / "sys_monitor.csv"
    print(f"[sys_monitor] Writing to {csv_path}  (interval={args.interval}s)", flush=True)

    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        fh.flush()

        while not _STOP and not sentinel.exists():
            mem   = psutil.virtual_memory()
            cores: list[float] = psutil.cpu_percent(percpu=True)  # type: ignore[assignment]

            vu = vt = 0.0
            if _GPU:
                try:
                    h    = pynvml.nvmlDeviceGetHandleByIndex(0)
                    info = pynvml.nvmlDeviceGetMemoryInfo(h)
                    vu   = info.used  / 1_048_576
                    vt   = info.total / 1_048_576
                except Exception:
                    pass

            s_ram, s_cpu = _health(args.health_url)

            writer.writerow({
                "ts":               datetime.now(timezone.utc).isoformat(),
                "cpu_avg_pct":      round(sum(cores) / len(cores) if cores else 0.0, 2),
                "cpu_per_core_json": json.dumps([round(c, 1) for c in cores]),
                "ram_used_mb":      round(mem.used  / 1_048_576, 1),
                "ram_total_mb":     round(mem.total / 1_048_576, 1),
                "ram_pct":          round(mem.percent, 1),
                "vram_used_mb":     round(vu, 1),
                "vram_total_mb":    round(vt, 1),
                "service_ram_mb":   s_ram,
                "service_cpu_avg":  s_cpu,
            })
            fh.flush()
            time.sleep(args.interval)

    print(f"[sys_monitor] Done. {csv_path}", flush=True)


if __name__ == "__main__":
    main()
