#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

"""Unit tests for the wdl_bench parser."""

import os
import tempfile
import unittest

from parser import (
    WdlBenchParser,
    average_perf_in_range,
    parse_interval_log,
    parse_perf_csv,
)


INTERVAL_LOG_SAMPLE = """START name=hash_benchmark t_us=1000000
END name=hash_benchmark t_us=3500000
START name=lzbench t_us=4000000
END name=lzbench t_us=5500000
"""


PERF_CSV_SAMPLE = (
    "1.5,1000,,cycles,,1.00,100.00\n"
    "1.5,500,,instructions,,1.00,100.00\n"
    "2.5,2000,,cycles,,1.00,100.00\n"
    "2.5,1500,,instructions,,1.00,100.00\n"
    "4.5,3000,,cycles,,1.00,100.00\n"
    "4.5,2500,,instructions,,1.00,100.00\n"
)


class TestWdlParser(unittest.TestCase):
    def test_parse_interval_log_pairs_start_end(self):
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".log") as f:
            f.write(INTERVAL_LOG_SAMPLE)
            path = f.name
        try:
            kernels = parse_interval_log(path)
            self.assertEqual(len(kernels), 2)
            self.assertEqual(kernels[0][0], "hash_benchmark")
            self.assertAlmostEqual(kernels[0][1], 1.0)
            self.assertAlmostEqual(kernels[0][2], 3.5)
            self.assertEqual(kernels[1][0], "lzbench")
            self.assertAlmostEqual(kernels[1][1], 4.0)
            self.assertAlmostEqual(kernels[1][2], 5.5)
        finally:
            os.unlink(path)

    def test_parse_perf_csv_keys_by_raw_t(self):
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".csv") as f:
            f.write(PERF_CSV_SAMPLE)
            path = f.name
        try:
            rows = parse_perf_csv(path)
            self.assertIn(1.5, rows)
            self.assertIn(2.5, rows)
            self.assertIn(4.5, rows)
            self.assertAlmostEqual(rows[1.5]["cycles"], 1000.0)
            self.assertAlmostEqual(rows[2.5]["instructions"], 1500.0)
        finally:
            os.unlink(path)

    def test_average_perf_in_range_filters_by_kernel_window(self):
        rows = parse_perf_csv_from_string(PERF_CSV_SAMPLE)
        # Kernel 1.0..3.5 should include rows at 1.5 and 2.5.
        means = average_perf_in_range(rows, 1.0, 3.5)
        self.assertAlmostEqual(means["cycles"], (1000 + 2000) / 2)
        self.assertAlmostEqual(means["instructions"], (500 + 1500) / 2)

    def test_full_pipeline_writes_csv_with_perf_columns(self):
        with tempfile.TemporaryDirectory() as d:
            ilog = os.path.join(d, "interval_log.txt")
            pcsv = os.path.join(d, "perf_wdl.csv")
            ocsv = os.path.join(d, "interval_metrics.csv")
            with open(ilog, "w") as f:
                f.write(INTERVAL_LOG_SAMPLE)
            with open(pcsv, "w") as f:
                f.write(PERF_CSV_SAMPLE)
            p = WdlBenchParser(
                interval_log=ilog, perf_csv=pcsv, output_csv=ocsv
            )
            summary = p.write_interval_metrics_csv()
            self.assertEqual(summary["total_kernels"], 2)
            with open(ocsv) as f:
                content = f.read()
            self.assertIn("hash_benchmark", content)
            self.assertIn("lzbench", content)
            self.assertIn("cycles", content)


def parse_perf_csv_from_string(s: str):
    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".csv") as f:
        f.write(s)
        path = f.name
    try:
        return parse_perf_csv(path)
    finally:
        os.unlink(path)


if __name__ == "__main__":
    unittest.main()
