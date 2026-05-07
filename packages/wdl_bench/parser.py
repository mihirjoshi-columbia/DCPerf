#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

"""Parser for wdl_bench's interval reporting outputs.

The wdl benchmark suite is a collection of CPU-bound microbenchmarks (folly
Benchmark, lzbench, openssl). Individual kernels are too tight to instrument
in-loop without an invasive folly patch, so for "uniform" interval reporting
we keep the same OUTPUT SHAPE (interval CSV + perf-stat sidecar) but use a
per-kernel time bucket: one row per kernel invocation.

Inputs read by this module:

- ``interval_log.txt`` -- written by ``run.sh`` when ``--window > 0``. Each
  line is either ``START name=<kernel> t_us=<...>`` or
  ``END name=<kernel> t_us=<...>`` (relative to the benchmark suite start).
- ``perf_wdl.csv`` -- written by the shared perf_sampler sidecar. One row
  per ``perf stat -I window*1000 -x ,`` interval; first column is the
  perf-relative t_sec, subsequent columns are value, unit, event, ...

Output:

- ``interval_metrics.csv`` -- one row per kernel with the kernel name, its
  start/end relative timestamps, and the average of every perf event that
  fell inside the kernel's wall-clock window.
"""

import os
from typing import Dict, List, Optional, Tuple


class KernelEvent:
    """Either a START or END marker for one kernel invocation."""

    def __init__(self, kind: str, name: str, t_sec: float):
        self.kind = kind  # "START" or "END"
        self.name = name
        self.t_sec = t_sec


def parse_interval_log(path: str) -> List[Tuple[str, float, float]]:
    """Read ``interval_log.txt`` and pair START/END events into kernel rows.

    Returns a list of ``(name, start_sec, end_sec)`` tuples in the order
    they appeared. We tolerate orphan markers (a kernel may have a START
    without an END if the suite was killed); they're skipped.
    """
    events: List[KernelEvent] = []
    if not os.path.exists(path):
        return []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            head, _, rest = line.partition(" ")
            kind = head
            if kind not in ("START", "END"):
                continue
            kv = dict(
                tok.split("=", 1) for tok in rest.split() if "=" in tok
            )
            name = kv.get("name", "")
            try:
                t_us = int(kv.get("t_us", "0"))
            except ValueError:
                t_us = 0
            events.append(KernelEvent(kind, name, t_us / 1.0e6))

    out: List[Tuple[str, float, float]] = []
    pending: Dict[str, float] = {}
    for ev in events:
        if ev.kind == "START":
            pending[ev.name] = ev.t_sec
        elif ev.kind == "END" and ev.name in pending:
            out.append((ev.name, pending.pop(ev.name), ev.t_sec))
    return out


def parse_perf_csv(path: str) -> Dict[float, Dict[str, float]]:
    """Bucket the perf-stat CSV by its first-column timestamp.

    The CSV is *not* aligned to any particular window other than what
    ``perf stat -I`` produced; the keys here are the raw perf-relative
    seconds floats. Down-stream we just do a range query against the
    kernel start/end timestamps, so no bucketization is needed."""
    rows: Dict[float, Dict[str, float]] = {}
    if not os.path.exists(path):
        return rows
    with open(path) as f:
        for line in f:
            if not line or line.startswith("#"):
                continue
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 4:
                continue
            try:
                t = float(parts[0])
            except ValueError:
                continue
            try:
                val = float(parts[1].replace("<not counted>", "0"))
            except ValueError:
                val = 0.0
            event = parts[3]
            rows.setdefault(t, {})[event] = val
    return rows


def average_perf_in_range(
    perf_rows: Dict[float, Dict[str, float]],
    start: float,
    end: float,
) -> Dict[str, float]:
    in_range = [
        row for t, row in perf_rows.items() if start <= t <= end
    ]
    if not in_range:
        return {}
    agg: Dict[str, list] = {}
    for row in in_range:
        for k, v in row.items():
            agg.setdefault(k, []).append(v)
    return {k: sum(v) / len(v) for k, v in agg.items() if v}


class WdlBenchParser:
    def __init__(
        self,
        interval_log: str = "interval_log.txt",
        perf_csv: str = "perf_wdl.csv",
        output_csv: str = "interval_metrics.csv",
    ):
        self.interval_log = interval_log
        self.perf_csv = perf_csv
        self.output_csv = output_csv

    def write_interval_metrics_csv(self) -> Dict[str, float]:
        """Join per-kernel timestamps with perf rows and write the CSV.

        Returns a dict of summary metrics for the final-results JSON.
        """
        kernels = parse_interval_log(self.interval_log)
        perf = parse_perf_csv(self.perf_csv)

        # Discover all event names across the whole run.
        events = sorted({k for row in perf.values() for k in row.keys()})
        header = ["kernel", "start_t_sec", "end_t_sec", "duration_s"] + events
        lines = [",".join(header) + "\n"]

        means_per_event: Dict[str, list] = {}
        for name, start, end in kernels:
            row = [name, f"{start:.6f}", f"{end:.6f}", f"{end - start:.6f}"]
            kernel_means = average_perf_in_range(perf, start, end)
            for ev in events:
                v = kernel_means.get(ev)
                row.append("" if v is None else str(v))
                if v is not None:
                    means_per_event.setdefault(ev, []).append(v)
            lines.append(",".join(row) + "\n")

        with open(self.output_csv, "w") as f:
            f.writelines(lines)

        summary: Dict[str, float] = {"total_kernels": len(kernels)}
        if means_per_event:
            cycles = self._mean(means_per_event.get("cycles"))
            instructions = self._mean(means_per_event.get("instructions"))
            cache_refs = self._mean(means_per_event.get("cache-references"))
            cache_misses = self._mean(means_per_event.get("cache-misses"))
            if cycles and cycles > 0:
                summary["mean_ipc"] = instructions / cycles
            if cache_refs and cache_refs > 0:
                summary["mean_llc_miss_rate"] = cache_misses / cache_refs
        return summary

    @staticmethod
    def _mean(vals: Optional[List[float]]) -> Optional[float]:
        if not vals:
            return None
        return sum(vals) / len(vals)
