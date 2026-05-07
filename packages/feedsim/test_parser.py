#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

"""Unit tests for the feedsim parser."""

import os
import tempfile
import unittest

from parser import (
    FeedsimIntervalSnapshot,
    FeedsimParser,
    parse_driver_log,
    parse_perf_csv,
    write_interval_metrics_csv,
)


DRIVER_LOG_SAMPLE = """Some preamble line
INTERVAL t=10.000 qps=1234.50 avg_us=850.20 p50_us=800.00 p95_us=1500.00 p99_us=2100.00
INTERVAL t=20.000 qps=1300.00 avg_us=900.00 p50_us=850.00 p95_us=1600.00 p99_us=2200.00
Some unrelated line
INTERVAL t=30.000 qps=1310.00 avg_us=910.00 p50_us=860.00 p95_us=1610.00 p99_us=2300.00
final requested_qps = 1300.00
"""


PERF_CSV_SAMPLE = (
    "10.001,5000,,cycles,,1.00,100.00\n"
    "10.001,2500,,instructions,,1.00,100.00\n"
    "10.001,100,,cache-references,,1.00,100.00\n"
    "10.001,10,,cache-misses,,1.00,100.00\n"
    "20.001,6000,,cycles,,1.00,100.00\n"
    "20.001,3000,,instructions,,1.00,100.00\n"
    "20.001,200,,cache-references,,1.00,100.00\n"
    "20.001,30,,cache-misses,,1.00,100.00\n"
)


class TestFeedsimSnapshot(unittest.TestCase):
    def test_from_line_valid(self):
        s = FeedsimIntervalSnapshot.from_line(
            "INTERVAL t=10.000 qps=1234.50 avg_us=850.20 "
            "p50_us=800.00 p95_us=1500.00 p99_us=2100.00"
        )
        self.assertIsNotNone(s)
        self.assertAlmostEqual(s.t_sec, 10.0)
        self.assertAlmostEqual(s.qps, 1234.5)
        self.assertAlmostEqual(s.avg_us, 850.2)
        self.assertAlmostEqual(s.p99_us, 2100.0)

    def test_from_line_invalid(self):
        self.assertIsNone(
            FeedsimIntervalSnapshot.from_line("garbage line")
        )
        self.assertIsNone(
            FeedsimIntervalSnapshot.from_line(
                "INTERVAL t=NOTANUM qps=1.0 avg_us=2.0 "
                "p50_us=3.0 p95_us=4.0 p99_us=5.0"
            )
        )


class TestFeedsimParser(unittest.TestCase):
    def test_parse_driver_log_filters_only_interval_lines(self):
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".log") as f:
            f.write(DRIVER_LOG_SAMPLE)
            path = f.name
        try:
            snaps = parse_driver_log(path)
            self.assertEqual(len(snaps), 3)
            self.assertAlmostEqual(snaps[1].qps, 1300.0)
        finally:
            os.unlink(path)

    def test_parse_perf_csv_buckets_correctly(self):
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".csv") as f:
            f.write(PERF_CSV_SAMPLE)
            path = f.name
        try:
            buckets = parse_perf_csv(path, window_sec=10)
            self.assertIn(10, buckets)
            self.assertIn(20, buckets)
            self.assertAlmostEqual(buckets[10]["cycles"], 5000.0)
            self.assertAlmostEqual(buckets[20]["instructions"], 3000.0)
        finally:
            os.unlink(path)

    def test_write_interval_metrics_csv_summary(self):
        snaps = [
            FeedsimIntervalSnapshot(10.0, 1000.0, 800, 750, 1400, 2000),
            FeedsimIntervalSnapshot(20.0, 1100.0, 850, 800, 1500, 2100),
        ]
        perf_rows = {
            10: {"cycles": 1000.0, "instructions": 500.0,
                 "cache-references": 100.0, "cache-misses": 10.0},
            20: {"cycles": 2000.0, "instructions": 1500.0,
                 "cache-references": 200.0, "cache-misses": 30.0},
        }
        with tempfile.TemporaryDirectory() as d:
            ocsv = os.path.join(d, "interval_metrics.csv")
            summary, events = write_interval_metrics_csv(
                snaps, perf_rows, window_sec=10, path=ocsv
            )
            self.assertAlmostEqual(summary["mean_qps"], 1050.0)
            self.assertAlmostEqual(summary["mean_p99_us"], 2050.0)
            # ipc = mean(cycles)/mean(instructions) inverted: instructions/cycles
            # means: cycles=1500, instructions=1000 → ipc = 1000/1500 = 0.667
            self.assertAlmostEqual(summary["ipc"], 1000 / 1500)
            # llc_miss_rate = cache-misses / cache-references
            self.assertAlmostEqual(summary["llc_miss_rate"], 20 / 150)
            with open(ocsv) as f:
                content = f.read()
            self.assertIn("cycles", content)
            self.assertIn("1000.00", content)

    def test_full_pipeline_writes_both_csvs(self):
        with tempfile.TemporaryDirectory() as d:
            dlog = os.path.join(d, "driver_intervals.log")
            pcsv = os.path.join(d, "perf.csv")
            ccsv = os.path.join(d, "client.csv")
            icsv = os.path.join(d, "interval_metrics.csv")
            with open(dlog, "w") as f:
                f.write(DRIVER_LOG_SAMPLE)
            with open(pcsv, "w") as f:
                f.write(PERF_CSV_SAMPLE)
            p = FeedsimParser(
                driver_log=dlog,
                perf_csv=pcsv,
                window_sec=10,
                client_csv=ccsv,
                interval_csv=icsv,
            )
            summary = p.run()
            self.assertIn("mean_qps", summary)
            self.assertTrue(os.path.exists(ccsv))
            self.assertTrue(os.path.exists(icsv))


if __name__ == "__main__":
    unittest.main()
