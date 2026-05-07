#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

"""Perf-stat sidecar for TaoBench interval reporting.

When ``--window=<sec>`` is enabled on the server side, ``run.py`` spawns one
``PerfSampler`` per server instance. The sampler shells out to::

    perf stat -I <window_ms> -x , -o <output_csv> -e <events> -- sleep <duration>

so that ``perf`` itself emits one line per window-aligned interval into a
machine-readable CSV. The ``parser`` module joins this CSV with the server and
client time-series on ``t_sec``.
"""

import os
import shlex
import subprocess
from typing import List, Optional


# Default event set picked to give a useful "system health" view without
# requiring uncore PMUs or root-only events. Override via the events= kwarg
# or the DCPERF_PERF_EVENTS env var (comma-separated).
DEFAULT_EVENTS: List[str] = [
    "task-clock",
    "cycles",
    "instructions",
    "cache-references",
    "cache-misses",
    "LLC-load-misses",
    "branch-misses",
]


def resolve_events(events: Optional[List[str]] = None) -> List[str]:
    if events:
        return list(events)
    env = os.environ.get("DCPERF_PERF_EVENTS", "").strip()
    if env:
        return [e.strip() for e in env.split(",") if e.strip()]
    return list(DEFAULT_EVENTS)


class PerfSampler:
    def __init__(
        self,
        output_csv: str,
        window_sec: int,
        duration_sec: int,
        events: Optional[List[str]] = None,
        cpu_list: Optional[str] = None,
        perf_binary: str = "perf",
    ):
        if window_sec <= 0:
            raise ValueError("window_sec must be > 0")
        if duration_sec <= 0:
            raise ValueError("duration_sec must be > 0")
        self.output_csv = output_csv
        self.window_sec = window_sec
        self.duration_sec = duration_sec
        self.events = resolve_events(events)
        self.cpu_list = cpu_list
        self.perf_binary = perf_binary
        self._proc: Optional[subprocess.Popen] = None

    def build_cmd(self) -> List[str]:
        cmd = [
            self.perf_binary,
            "stat",
            "-I",
            str(self.window_sec * 1000),
            "-x",
            ",",
            "-o",
            self.output_csv,
        ]
        if self.cpu_list:
            cmd += ["-C", self.cpu_list]
        else:
            cmd += ["-a"]
        if self.events:
            cmd += ["-e", ",".join(self.events)]
        cmd += ["--", "sleep", str(self.duration_sec)]
        return cmd

    def start(self) -> None:
        cmd = self.build_cmd()
        # Best-effort: if the user lacks perf privileges we just leave a stub
        # CSV so the parser knows the sampler ran but produced no rows.
        try:
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            self._proc = None
            with open(self.output_csv, "w") as f:
                f.write("# perf binary not found; perf-stat sidecar disabled\n")

    def stop(self, timeout: float = 5.0) -> None:
        if self._proc is None:
            return
        if self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait()
        self._proc = None

    def cmd_string(self) -> str:
        return " ".join(shlex.quote(p) for p in self.build_cmd())

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *args):
        self.stop()
        return False


def perf_csv_path_for_instance(bm_dir: str, instance_idx: int) -> str:
    return os.path.join(bm_dir, f"perf_{instance_idx}.csv")
