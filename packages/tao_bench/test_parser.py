#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

"""Unit tests for ``packages/tao_bench/parser.py``.

These tests cover the new interval-reporting paths so changes to
``TaoBenchClientIntervalSnapshot``, ``TaoBenchParser.parse_perf_csv``, and
the latency-aggregation helpers don't regress silently.

Run from this directory with::

    python3 -m unittest test_parser
"""

import os
import tempfile
import unittest

from parser import TaoBenchClientIntervalSnapshot, TaoBenchParser


class TestClientIntervalSnapshot(unittest.TestCase):
    def test_valid_line_parses_all_fields(self):
        line = (
            "INTERVAL t=10 set_qps=0 get_qps=12345.6 hit_rate=0.9 "
            "avg_us=42 p50_us=30 p99_us=180 p999_us=900 max_us=2000\n"
        )
        snap = TaoBenchClientIntervalSnapshot(line)
        self.assertTrue(snap.valid)
        self.assertEqual(snap.get("t"), 10.0)
        self.assertEqual(snap.get("get_qps"), 12345.6)
        self.assertEqual(snap.get("p99_us"), 180.0)
        self.assertEqual(snap.get("max_us"), 2000.0)

    def test_unrelated_line_is_invalid(self):
        snap = TaoBenchClientIntervalSnapshot("Sets 1 2 3\n")
        self.assertFalse(snap.valid)

    def test_partial_line_is_invalid(self):
        snap = TaoBenchClientIntervalSnapshot("INTERVAL t=10 get_qps=100\n")
        self.assertFalse(snap.valid)

    def test_extra_unknown_keys_ignored(self):
        line = (
            "INTERVAL t=5 set_qps=0 get_qps=10 hit_rate=0.9 avg_us=1 p50_us=1 "
            "p99_us=1 p999_us=1 max_us=1 future_field=ignored\n"
        )
        snap = TaoBenchClientIntervalSnapshot(line)
        self.assertTrue(snap.valid)


class TestPerfCsvParsing(unittest.TestCase):
    def test_well_formed_perf_csv(self):
        sample = (
            "# perf stat --event=cycles,instructions -- sleep 10\n"
            "1.001234567,123456789,,cycles,1234,100.00\n"
            "1.001234567,98765432,,instructions,1234,100.00\n"
            "2.001234567,234567890,,cycles,1234,100.00\n"
            "2.001234567,98765400,,instructions,1234,100.00\n"
        )
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".csv") as tmp:
            tmp.write(sample)
            path = tmp.name
        try:
            rows = TaoBenchParser.parse_perf_csv(path)
        finally:
            os.unlink(path)
        self.assertIn(1.001234567, rows)
        self.assertEqual(rows[1.001234567]["cycles"], 123456789.0)
        self.assertEqual(rows[2.001234567]["instructions"], 98765400.0)

    def test_missing_file_returns_empty_dict(self):
        rows = TaoBenchParser.parse_perf_csv("/nonexistent/perf.csv")
        self.assertEqual(rows, {})

    def test_not_counted_value_is_zero(self):
        sample = "1.0,<not counted>,,cycles,1,100.0\n"
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".csv") as tmp:
            tmp.write(sample)
            path = tmp.name
        try:
            rows = TaoBenchParser.parse_perf_csv(path)
        finally:
            os.unlink(path)
        self.assertEqual(rows[1.0]["cycles"], 0.0)


class TestProcessClientIntervals(unittest.TestCase):
    def test_aggregate_metrics(self):
        snaps = [
            TaoBenchClientIntervalSnapshot(
                "INTERVAL t=5 set_qps=0 get_qps=10 hit_rate=0.9 "
                "avg_us=10 p50_us=5 p99_us=50 p999_us=100 max_us=200\n"
            ),
            TaoBenchClientIntervalSnapshot(
                "INTERVAL t=10 set_qps=0 get_qps=12 hit_rate=0.91 "
                "avg_us=20 p50_us=15 p99_us=80 p999_us=200 max_us=400\n"
            ),
        ]
        for s in snaps:
            self.assertTrue(s.valid)
        metrics = {}
        TaoBenchParser.process_client_intervals(metrics, snaps)
        self.assertEqual(metrics["client_intervals"], 2)
        self.assertEqual(metrics["client_avg_us"], 15.0)
        self.assertEqual(metrics["client_max_us"], 400.0)
        self.assertEqual(metrics["client_p50_us_avg"], 10.0)


if __name__ == "__main__":
    unittest.main()
