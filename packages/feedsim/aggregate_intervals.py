#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

"""Aggregate per-instance ``interval_metrics_<port>.csv`` files into a
single ``interval_metrics_overall.csv`` for feedsim multi-inst runs.

Mirrors ``packages/tao_bench/run_autoscale.py:aggregate_interval_metrics``:

  - throughput-like columns (qps) are summed across instances per t_sec
  - latency-like columns (avg_us, p50_us, p95_us, p99_us) are
    qps-weighted-averaged when possible, else simple-averaged
  - perf counters are averaged across instances
"""

import csv
import glob
import os
import sys
from collections import defaultdict
from typing import Dict, List


THROUGHPUT_COLS = {"qps"}
LATENCY_COLS = {"avg_us", "p50_us", "p95_us", "p99_us"}


def main(feedsim_root: str) -> int:
    pattern = os.path.join(feedsim_root, "interval_metrics_*.csv")
    files = [
        f for f in glob.glob(pattern)
        if "overall" not in os.path.basename(f)
    ]
    if not files:
        print(f"aggregate_intervals: no inputs matched {pattern}")
        return 0

    # Read every per-instance CSV. Bucket rows by t_sec key.
    by_t: Dict[float, List[dict]] = defaultdict(list)
    headers_seen = []
    for path in files:
        with open(path, newline="") as f:
            reader = csv.DictReader(f)
            if not headers_seen and reader.fieldnames:
                headers_seen = list(reader.fieldnames)
            for row in reader:
                try:
                    t = float(row["t_sec"])
                except (KeyError, ValueError):
                    continue
                by_t[t].append(row)

    if not headers_seen:
        print("aggregate_intervals: no rows found")
        return 0

    # Discover columns. Anything that's not t_sec/throughput/latency we
    # treat as a perf counter and average across instances.
    out_path = os.path.join(feedsim_root, "interval_metrics_overall.csv")
    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers_seen)
        writer.writeheader()
        for t in sorted(by_t.keys()):
            rows = by_t[t]
            agg: Dict[str, str] = {"t_sec": str(t)}
            qps_total = 0.0
            for r in rows:
                v = _safe_float(r.get("qps", ""))
                qps_total += v
            for col in headers_seen:
                if col == "t_sec":
                    continue
                if col in THROUGHPUT_COLS:
                    agg[col] = f"{qps_total:.2f}"
                elif col in LATENCY_COLS:
                    if qps_total > 0:
                        num = sum(
                            _safe_float(r.get(col, ""))
                            * _safe_float(r.get("qps", ""))
                            for r in rows
                        )
                        agg[col] = f"{(num / qps_total):.2f}"
                    else:
                        vals = [
                            _safe_float(r.get(col, "")) for r in rows
                        ]
                        agg[col] = (
                            f"{(sum(vals) / len(vals)):.2f}" if vals else ""
                        )
                else:
                    vals = [
                        _safe_float(r.get(col, ""))
                        for r in rows
                        if r.get(col)
                    ]
                    agg[col] = (
                        f"{(sum(vals) / len(vals))}" if vals else ""
                    )
            writer.writerow(agg)
    print(f"aggregate_intervals: wrote {out_path}")
    return 0


def _safe_float(s: str) -> float:
    try:
        return float(s)
    except (TypeError, ValueError):
        return 0.0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "."))
