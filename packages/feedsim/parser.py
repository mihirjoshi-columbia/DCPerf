#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

"""Parser for feedsim's interval reporting outputs.

When ``--window=<sec>`` is enabled on DriverNodeRank, the C++ binary writes
one line per window to its stdout::

    INTERVAL t=<sec> qps=<...> avg_us=<...> p50_us=<...> p95_us=<...> p99_us=<...>

``run.sh`` arranges for these to be teed into ``driver_intervals.log`` via
``FEEDSIM_DRIVER_LOG`` so the search-QPS loop's iterations don't drop them.

This module reads that log + the perf_sampler.py sidecar CSV
(``perf_<port>.csv``) and writes ``interval_metrics.csv``, plus a summary
dict suitable for the final-results JSON (``mean_qps``, ``mean_p99_us``,
``ipc``, ``llc_miss_rate``).
"""

import os
import re
from typing import Dict, List, Optional, Tuple


# Match: INTERVAL t=<sec> qps=<...> avg_us=<...> p50_us=<...> p95_us=<...> p99_us=<...>
INTERVAL_RE = re.compile(
    r"INTERVAL\s+"
    r"t=(?P<t>[0-9.]+)\s+"
    r"qps=(?P<qps>[0-9.]+)\s+"
    r"avg_us=(?P<avg>[0-9.]+)\s+"
    r"p50_us=(?P<p50>[0-9.]+)\s+"
    r"p95_us=(?P<p95>[0-9.]+)\s+"
    r"p99_us=(?P<p99>[0-9.]+)"
)


class FeedsimIntervalSnapshot:
    def __init__(
        self,
        t_sec: float,
        qps: float,
        avg_us: float,
        p50_us: float,
        p95_us: float,
        p99_us: float,
    ):
        self.t_sec = t_sec
        self.qps = qps
        self.avg_us = avg_us
        self.p50_us = p50_us
        self.p95_us = p95_us
        self.p99_us = p99_us

    @classmethod
    def from_line(cls, line: str) -> Optional["FeedsimIntervalSnapshot"]:
        m = INTERVAL_RE.search(line)
        if not m:
            return None
        try:
            return cls(
                t_sec=float(m.group("t")),
                qps=float(m.group("qps")),
                avg_us=float(m.group("avg")),
                p50_us=float(m.group("p50")),
                p95_us=float(m.group("p95")),
                p99_us=float(m.group("p99")),
            )
        except ValueError:
            return None


def parse_driver_log(path: str) -> List[FeedsimIntervalSnapshot]:
    """Parse driver_intervals.log into a list of snapshots.

    The log is the concatenation of multiple driver runs (the search-QPS
    loop runs the driver once per probe iteration); we keep every snapshot
    in order so the CSV reflects every probe's per-window stats."""
    out: List[FeedsimIntervalSnapshot] = []
    if not os.path.exists(path):
        return out
    with open(path) as f:
        for line in f:
            snap = FeedsimIntervalSnapshot.from_line(line)
            if snap is not None:
                out.append(snap)
    return out


def parse_perf_csv(path: str, window_sec: int) -> Dict[int, Dict[str, float]]:
    """Bucket the perf-stat CSV on window-aligned t_sec keys."""
    rows: Dict[int, Dict[str, float]] = {}
    if not os.path.exists(path) or window_sec <= 0:
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
            bucket = int(t // window_sec) * window_sec
            try:
                val = float(parts[1].replace("<not counted>", "0"))
            except ValueError:
                val = 0.0
            event = parts[3]
            rows.setdefault(bucket, {})[event] = val
    return rows


def write_client_csv(snaps: List[FeedsimIntervalSnapshot], path: str) -> None:
    header = "t_sec,qps,avg_us,p50_us,p95_us,p99_us\n"
    with open(path, "w") as f:
        f.write(header)
        for s in snaps:
            f.write(
                f"{s.t_sec:.3f},{s.qps:.2f},{s.avg_us:.2f},"
                f"{s.p50_us:.2f},{s.p95_us:.2f},{s.p99_us:.2f}\n"
            )


def write_interval_metrics_csv(
    snaps: List[FeedsimIntervalSnapshot],
    perf_rows: Dict[int, Dict[str, float]],
    window_sec: int,
    path: str,
) -> Tuple[Dict[str, float], List[str]]:
    """Join client INTERVAL snapshots with perf rows on window-aligned t_sec.

    Returns (summary, events). ``summary`` aggregates mean qps + mean p99
    across the run, plus the standard ipc / llc_miss_rate from the perf
    rows. ``events`` is the list of perf event column names included.
    """
    events = sorted({k for row in perf_rows.values() for k in row.keys()})
    header_cols = (
        ["t_sec", "qps", "avg_us", "p50_us", "p95_us", "p99_us"] + events
    )
    lines = [",".join(header_cols) + "\n"]

    perf_means_acc: Dict[str, list] = {}
    qps_acc: List[float] = []
    p99_acc: List[float] = []
    for s in snaps:
        bucket = (
            int(s.t_sec // window_sec) * window_sec if window_sec > 0 else 0
        )
        perf_row = perf_rows.get(bucket, {})
        row = [
            f"{s.t_sec:.3f}",
            f"{s.qps:.2f}",
            f"{s.avg_us:.2f}",
            f"{s.p50_us:.2f}",
            f"{s.p95_us:.2f}",
            f"{s.p99_us:.2f}",
        ]
        for ev in events:
            v = perf_row.get(ev)
            row.append("" if v is None else str(v))
            if v is not None:
                perf_means_acc.setdefault(ev, []).append(v)
        lines.append(",".join(row) + "\n")
        if s.qps > 0:
            qps_acc.append(s.qps)
        if s.p99_us > 0:
            p99_acc.append(s.p99_us)

    with open(path, "w") as f:
        f.writelines(lines)

    summary: Dict[str, float] = {}
    if qps_acc:
        summary["mean_qps"] = sum(qps_acc) / len(qps_acc)
    if p99_acc:
        summary["mean_p99_us"] = sum(p99_acc) / len(p99_acc)
    if perf_means_acc:
        means = {k: sum(v) / len(v) for k, v in perf_means_acc.items() if v}
        cycles = means.get("cycles", 0)
        instructions = means.get("instructions", 0)
        cache_refs = means.get("cache-references", 0)
        cache_misses = means.get("cache-misses", 0)
        if cycles > 0:
            summary["ipc"] = instructions / cycles
        if cache_refs > 0:
            summary["llc_miss_rate"] = cache_misses / cache_refs
        summary["perf_event_means"] = means
    return summary, events


class FeedsimParser:
    """High-level entry point matched to run.sh's expected interface."""

    def __init__(
        self,
        driver_log: str = "driver_intervals.log",
        perf_csv: str = "perf.csv",
        window_sec: int = 0,
        client_csv: str = "client.csv",
        interval_csv: str = "interval_metrics.csv",
    ):
        self.driver_log = driver_log
        self.perf_csv = perf_csv
        self.window_sec = window_sec
        self.client_csv = client_csv
        self.interval_csv = interval_csv

    def run(self) -> Dict[str, float]:
        snaps = parse_driver_log(self.driver_log)
        perf_rows = parse_perf_csv(self.perf_csv, self.window_sec)
        write_client_csv(snaps, self.client_csv)
        summary, _events = write_interval_metrics_csv(
            snaps, perf_rows, self.window_sec, self.interval_csv
        )
        return summary
