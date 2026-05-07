#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

"""Parser for video_transcode_bench's interval reporting outputs.

When ``--window=<sec>`` is enabled, ``run.sh`` injects ``-progress
file:progress_<i>.log`` into every ffmpeg invocation. ffmpeg writes a
``key=value`` block roughly every 500ms with fields like:

    frame=124
    fps=24.5
    bitrate=12345.6kbits/s
    total_size=987654
    out_time_us=12345678
    out_time=00:00:12.345678
    speed=1.23x
    progress=continue

This module reads those files, buckets the rows by window-aligned
``t_sec``, and emits a per-encoder time-series CSV plus a unified
``interval_metrics.csv`` joining encoder rows with the perf-stat sidecar.
"""

import glob
import os
import re
from typing import Dict, List, Optional


PROGRESS_KEYS = (
    "frame",
    "fps",
    "bitrate",
    "out_time_us",
    "speed",
    "total_size",
)


class FfmpegProgressBlock:
    """One ``progress=...`` terminated block in an ffmpeg ``-progress`` log."""

    def __init__(self):
        self.fields: Dict[str, str] = {}

    def add(self, key: str, value: str) -> None:
        self.fields[key] = value

    @property
    def is_terminator(self) -> bool:
        # ffmpeg writes `progress=continue` mid-encode and `progress=end` once
        # finished; both close a record.
        return "progress" in self.fields

    def get_t_sec(self) -> Optional[float]:
        try:
            return float(self.fields.get("out_time_us", "")) / 1e6
        except ValueError:
            return None

    def get_fps(self) -> float:
        try:
            return float(self.fields.get("fps", "0"))
        except ValueError:
            return 0.0

    def get_bitrate_kbps(self) -> float:
        raw = self.fields.get("bitrate", "0kbits/s")
        m = re.match(r"([0-9.]+)\s*kbits/s", raw.strip())
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                return 0.0
        return 0.0

    def get_frame(self) -> int:
        try:
            return int(self.fields.get("frame", "0"))
        except ValueError:
            return 0

    def get_speed(self) -> float:
        raw = self.fields.get("speed", "0x").rstrip("x")
        try:
            return float(raw)
        except ValueError:
            return 0.0


def parse_progress_file(path: str) -> List[FfmpegProgressBlock]:
    blocks: List[FfmpegProgressBlock] = []
    cur = FfmpegProgressBlock()
    try:
        f = open(path)
    except OSError:
        return blocks
    with f:
        for line in f:
            if "=" not in line:
                continue
            key, _, value = line.strip().partition("=")
            cur.add(key.strip(), value.strip())
            if key.strip() == "progress":
                blocks.append(cur)
                cur = FfmpegProgressBlock()
    return blocks


def bucket_blocks_by_window(
    blocks: List[FfmpegProgressBlock], window_sec: int
) -> Dict[int, FfmpegProgressBlock]:
    """Pick the latest block within each window-aligned bucket. ffmpeg
    progress is monotonic so the latest block in a bucket is the
    most-up-to-date snapshot for that interval."""
    buckets: Dict[int, FfmpegProgressBlock] = {}
    for b in blocks:
        t = b.get_t_sec()
        if t is None or window_sec <= 0:
            continue
        bucket = int(t // window_sec) * window_sec
        buckets[bucket] = b
    return buckets


class VideoTranscodeParser:
    """High-level entry point used by ``run.sh`` post-processing."""

    def __init__(
        self,
        progress_glob: str = "progress_*.log",
        window_sec: int = 0,
        perf_csv_path: Optional[str] = None,
    ):
        self.progress_glob = progress_glob
        self.window_sec = window_sec
        self.perf_csv_path = perf_csv_path

    def parse_perf_csv(self) -> Dict[int, Dict[str, float]]:
        """Bucket the perf-stat CSV (perf stat -I window*1000 -x ,) on
        window-aligned t_sec keys, mirroring tao_bench's parser approach."""
        rows: Dict[int, Dict[str, float]] = {}
        if not self.perf_csv_path:
            return rows
        try:
            f = open(self.perf_csv_path)
        except OSError:
            return rows
        with f:
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
                bucket = int(t // self.window_sec) * self.window_sec
                event = parts[3]
                try:
                    val = float(parts[1].replace("<not counted>", "0"))
                except ValueError:
                    val = 0.0
                rows.setdefault(bucket, {})[event] = val
        return rows

    def write_interval_metrics_csv(self, output_path: str) -> Dict[str, float]:
        """Join progress + perf rows on t_sec bucket and write the unified CSV.

        Returns a dict of summary metrics suitable for the final-results JSON.
        """
        progress = self.collect_progress()
        perf = self.parse_perf_csv()

        events = sorted({k for row in perf.values() for k in row.keys()})
        header = (
            ["t_sec", "frames", "fps", "bitrate_kbps", "speed"] + events
        )
        lines = [",".join(header) + "\n"]
        all_t = sorted(set(progress.keys()) | set(perf.keys()))
        for t in all_t:
            blk = progress.get(t)
            row = [str(t)]
            row.append(str(blk.get_frame()) if blk else "")
            row.append(str(blk.get_fps()) if blk else "")
            row.append(str(blk.get_bitrate_kbps()) if blk else "")
            row.append(str(blk.get_speed()) if blk else "")
            perf_row = perf.get(t, {})
            for ev in events:
                row.append(str(perf_row.get(ev, "")))
            lines.append(",".join(row) + "\n")

        with open(output_path, "w") as f:
            f.writelines(lines)

        # Summary metrics
        summary: Dict[str, float] = {}
        if progress:
            blocks = list(progress.values())
            fps_vals = [b.get_fps() for b in blocks if b.get_fps() > 0]
            br_vals = [b.get_bitrate_kbps() for b in blocks if b.get_bitrate_kbps() > 0]
            if fps_vals:
                summary["mean_fps"] = sum(fps_vals) / len(fps_vals)
            if br_vals:
                summary["mean_bitrate_kbps"] = sum(br_vals) / len(br_vals)
        if perf:
            agg: Dict[str, list] = {}
            for row in perf.values():
                for k, v in row.items():
                    agg.setdefault(k, []).append(v)
            means = {k: sum(v) / len(v) for k, v in agg.items() if v}
            cycles = means.get("cycles", 0)
            instructions = means.get("instructions", 0)
            cache_refs = means.get("cache-references", 0)
            cache_misses = means.get("cache-misses", 0)
            if cycles > 0:
                summary["ipc"] = instructions / cycles
            if cache_refs > 0:
                summary["llc_miss_rate"] = cache_misses / cache_refs
            summary["perf_event_means"] = means
        return summary

    def collect_progress(self) -> Dict[int, FfmpegProgressBlock]:
        """Merge per-job progress files into a single time-bucketed dict.
        Multiple parallel ffmpeg jobs write into the same window; we sum
        their frames-per-window (throughput) and average their fps/speed."""
        files = sorted(glob.glob(self.progress_glob))
        per_job_buckets = []
        for path in files:
            blocks = parse_progress_file(path)
            per_job_buckets.append(
                bucket_blocks_by_window(blocks, self.window_sec)
            )

        merged: Dict[int, FfmpegProgressBlock] = {}
        all_buckets = sorted({b for d in per_job_buckets for b in d.keys()})
        for t in all_buckets:
            agg = FfmpegProgressBlock()
            frames = 0
            fps_total = 0.0
            br_total = 0.0
            speed_total = 0.0
            n = 0
            for d in per_job_buckets:
                b = d.get(t)
                if b is None:
                    continue
                frames += b.get_frame()
                fps_total += b.get_fps()
                br_total += b.get_bitrate_kbps()
                speed_total += b.get_speed()
                n += 1
            if n == 0:
                continue
            agg.add("out_time_us", str(int(t * 1e6)))
            agg.add("frame", str(frames))
            agg.add("fps", str(fps_total))
            agg.add("bitrate", f"{br_total:.2f}kbits/s")
            agg.add("speed", f"{speed_total / n:.3f}x")
            agg.add("progress", "continue")
            merged[t] = agg
        return merged
