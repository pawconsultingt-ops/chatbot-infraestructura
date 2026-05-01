"""
post_process.py  —  consolidate Locust + sys_monitor + request detail CSVs

Reads three sources for a given scenario result directory:
  1. locust_stats_history.csv   — aggregate latency / rps / user count per tick
  2. sys_monitor.csv            — system resource snapshots
  3. requests_detail.csv        — per-request metadata (category, tokens_in)

Joins all three on time buckets (configurable, default 10 s) using a
nearest-neighbor merge and writes consolidated.csv + a human-readable summary.

Usage:
    python shared/post_process.py --results results/scenario_1_baseline
    python shared/post_process.py --results results/scenario_2_ramp --bucket 30
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


# ── helpers ────────────────────────────────────────────────────────────────────

def _parse_ts(s: str) -> float:
    """ISO-8601 or Unix epoch float → Unix epoch float."""
    s = s.strip()
    if not s:
        return 0.0
    try:
        return float(s)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z",
                "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(s, fmt).timestamp()
        except ValueError:
            continue
    return 0.0


def _bucket(ts: float, size: int) -> int:
    return int(ts // size) * size


def _nearest(target: float, lookup: dict[int, dict], bucket_size: int) -> dict:
    """Find the closest bucket entry to target timestamp."""
    t_bucket = _bucket(target, bucket_size)
    for delta in (0, bucket_size, -bucket_size, 2 * bucket_size, -2 * bucket_size):
        row = lookup.get(t_bucket + delta)
        if row:
            return row
    return {}


# ── loaders ────────────────────────────────────────────────────────────────────

def _load_locust_history(path: Path) -> list[dict]:
    """
    Locust --csv generates *_stats_history.csv with columns:
      Timestamp, User count, Type, Name, Requests/s, Failures/s,
      50%ile (ms), 60%ile, 70%ile, 80%ile, 90%ile, 95%ile, 99%ile, ...
      Total Request Count, Total Failure Count
    We keep only Aggregated rows.
    """
    rows = []
    if not path.exists():
        return rows
    with path.open(encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            if row.get("Name", "").strip().lower() not in ("aggregated", "total"):
                continue
            ts_raw = row.get("Timestamp", "0")
            rows.append({
                "ts_epoch":   float(ts_raw) if ts_raw.replace(".", "").isdigit() else _parse_ts(ts_raw),
                "user_count": int(float(row.get("User count", 0) or 0)),
                "rps":        float(row.get("Requests/s", 0) or 0),
                "fail_rps":   float(row.get("Failures/s", 0) or 0),
                "p50_ms":     float(row.get("50%ile (ms)", 0) or 0),
                "p90_ms":     float(row.get("90%ile (ms)", 0) or 0),
                "p95_ms":     float(row.get("95%ile (ms)", 0) or 0),
                "p99_ms":     float(row.get("99%ile (ms)", 0) or 0),
                "total_req":  int(float(row.get("Total Request Count", 0) or 0)),
                "total_fail": int(float(row.get("Total Failure Count", 0) or 0)),
            })
    return rows


def _load_sys_monitor(path: Path, bucket_size: int) -> dict[int, dict]:
    """Returns {bucket_epoch: row_dict}."""
    lookup: dict[int, dict] = {}
    if not path.exists():
        return lookup
    with path.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            ts = _parse_ts(row.get("ts", ""))
            if ts == 0:
                continue
            b = _bucket(ts, bucket_size)
            lookup[b] = {
                "sys_cpu_avg":    float(row.get("cpu_avg_pct", 0) or 0),
                "sys_ram_mb":     float(row.get("ram_used_mb", 0) or 0),
                "sys_ram_pct":    float(row.get("ram_pct", 0) or 0),
                "sys_vram_mb":    float(row.get("vram_used_mb", 0) or 0),
                "svc_ram_mb":     float(row.get("service_ram_mb", 0) or 0),
                "svc_cpu_avg":    float(row.get("service_cpu_avg", 0) or 0),
                "cpu_per_core":   row.get("cpu_per_core_json", "[]"),
            }
    return lookup


def _load_request_detail(path: Path, bucket_size: int) -> dict[int, dict]:
    """Aggregates per-request detail into time buckets."""
    buckets: dict[int, list[dict]] = defaultdict(list)
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            ts = _parse_ts(row.get("ts", ""))
            if ts == 0:
                continue
            b = _bucket(ts, bucket_size)
            buckets[b].append({
                "category":    row.get("category", ""),
                "tokens_in":   int(float(row.get("tokens_in", 0) or 0)),
                "resp_ms":     float(row.get("response_time_ms", 0) or 0),
                "success":     row.get("success", "true").lower() == "true",
            })

    result: dict[int, dict] = {}
    for b, items in buckets.items():
        cat_counts: dict[str, int] = defaultdict(int)
        for it in items:
            cat_counts[it["category"]] += 1
        tokens = [it["tokens_in"] for it in items if it["tokens_in"] > 0]
        result[b] = {
            "detail_count":       len(items),
            "detail_errors":      sum(1 for it in items if not it["success"]),
            "detail_avg_tokens":  round(statistics.mean(tokens), 1) if tokens else 0,
            "cat_simple":         cat_counts.get("simple", 0),
            "cat_medium":         cat_counts.get("medium", 0),
            "cat_complex":        cat_counts.get("complex", 0),
            "cat_extreme":        cat_counts.get("extreme", 0),
        }
    return result


# ── consolidate ────────────────────────────────────────────────────────────────

CONSOLIDATED_FIELDS = [
    "ts_epoch", "ts_iso",
    # Locust aggregate
    "user_count", "rps", "fail_rps", "error_rate_pct",
    "p50_ms", "p90_ms", "p95_ms", "p99_ms",
    "total_req", "total_fail",
    # System
    "sys_cpu_avg", "sys_ram_mb", "sys_ram_pct", "sys_vram_mb",
    "svc_ram_mb", "svc_cpu_avg",
    # Request detail
    "detail_count", "detail_errors", "detail_avg_tokens",
    "cat_simple", "cat_medium", "cat_complex", "cat_extreme",
    # Derived
    "p95_vs_baseline", "above_breakpoint",
]


def consolidate(results_dir: Path, bucket_size: int = 10) -> Path:
    history_path = results_dir / "locust_stats_history.csv"
    sys_path     = results_dir / "sys_monitor.csv"
    detail_path  = results_dir / "requests_detail.csv"
    out_path     = results_dir / "consolidated.csv"

    print(f"[post_process] Reading {results_dir.name}…")
    history = _load_locust_history(history_path)
    sys_lut = _load_sys_monitor(sys_path, bucket_size)
    det_lut = _load_request_detail(detail_path, bucket_size)

    # Load baseline p95 if available (written by scenario 1)
    baseline_p95 = 0.0
    baseline_file = results_dir.parent / "scenario_1_baseline" / "baseline.json"
    if baseline_file.exists():
        try:
            baseline_p95 = json.loads(baseline_file.read_text())["p95_ms"]
        except Exception:
            pass

    print(f"  locust_history rows : {len(history)}")
    print(f"  sys_monitor buckets : {len(sys_lut)}")
    print(f"  request_detail buckets: {len(det_lut)}")
    if baseline_p95:
        print(f"  baseline p95        : {baseline_p95:.0f} ms")

    rows: list[dict] = []
    for h in history:
        ts  = h["ts_epoch"]
        sys = _nearest(ts, sys_lut, bucket_size)
        det = _nearest(ts, det_lut, bucket_size)

        total = h["total_req"]
        fail  = h["total_fail"]
        error_pct = round(fail / total * 100, 2) if total else 0.0
        p95_vs_bl = round(h["p95_ms"] / baseline_p95, 2) if baseline_p95 else None

        rows.append({
            "ts_epoch":         ts,
            "ts_iso":           datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
            "user_count":       h["user_count"],
            "rps":              round(h["rps"], 3),
            "fail_rps":         round(h["fail_rps"], 3),
            "error_rate_pct":   error_pct,
            "p50_ms":           h["p50_ms"],
            "p90_ms":           h["p90_ms"],
            "p95_ms":           h["p95_ms"],
            "p99_ms":           h["p99_ms"],
            "total_req":        total,
            "total_fail":       fail,
            **{k: sys.get(k, "") for k in ["sys_cpu_avg","sys_ram_mb","sys_ram_pct",
                                            "sys_vram_mb","svc_ram_mb","svc_cpu_avg"]},
            **{k: det.get(k, 0)  for k in ["detail_count","detail_errors","detail_avg_tokens",
                                            "cat_simple","cat_medium","cat_complex","cat_extreme"]},
            "p95_vs_baseline":  p95_vs_bl,
            "above_breakpoint": (p95_vs_bl is not None and p95_vs_bl > 3.0),
        })

    with out_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CONSOLIDATED_FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    print(f"  consolidated rows   : {len(rows)}")
    print(f"  Output              : {out_path}")

    # ── summary ──
    if rows:
        p95s = [r["p95_ms"] for r in rows if r["p95_ms"]]
        rpss = [r["rps"]    for r in rows if r["rps"]]
        errs = [r["error_rate_pct"] for r in rows]
        print("\n  === SUMMARY ===")
        print(f"  p95 latency  — min: {min(p95s):.0f}ms  max: {max(p95s):.0f}ms  avg: {statistics.mean(p95s):.0f}ms")
        print(f"  throughput   — max: {max(rpss):.2f} rps  avg: {statistics.mean(rpss):.2f} rps")
        print(f"  error rate   — max: {max(errs):.2f}%  avg: {statistics.mean(errs):.2f}%")
        if any(r["above_breakpoint"] for r in rows):
            bp = next(r for r in rows if r["above_breakpoint"])
            print(f"  breakpoint   — {bp['user_count']} users  p95={bp['p95_ms']:.0f}ms ({bp['p95_vs_baseline']:.1f}x baseline)")

    return out_path


# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", required=True, help="Scenario results directory")
    ap.add_argument("--bucket",  type=int, default=10, help="Time bucket size in seconds")
    args = ap.parse_args()
    consolidate(Path(args.results), args.bucket)
