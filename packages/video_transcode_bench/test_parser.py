#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

"""Unit tests for the video_transcode_bench parser."""

import os
import tempfile
import unittest

from parser import (
    FfmpegProgressBlock,
    VideoTranscodeParser,
    bucket_blocks_by_window,
    parse_progress_file,
)


PROGRESS_SAMPLE = """frame=120
fps=24.0
bitrate=12345.6kbits/s
total_size=987654
out_time_us=5000000
out_time=00:00:05.000000
speed=1.20x
progress=continue
frame=240
fps=24.0
bitrate=12345.6kbits/s
total_size=1975308
out_time_us=10000000
out_time=00:00:10.000000
speed=1.20x
progress=continue
frame=480
fps=24.0
bitrate=12345.6kbits/s
total_size=3950616
out_time_us=20000000
out_time=00:00:20.000000
speed=1.20x
progress=end
"""


class TestProgressParsing(unittest.TestCase):
    def test_parse_progress_file_extracts_three_blocks(self):
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".log") as f:
            f.write(PROGRESS_SAMPLE)
            path = f.name
        try:
            blocks = parse_progress_file(path)
            self.assertEqual(len(blocks), 3)
            self.assertEqual(blocks[0].get_frame(), 120)
            self.assertAlmostEqual(blocks[0].get_t_sec(), 5.0)
            self.assertAlmostEqual(blocks[1].get_t_sec(), 10.0)
            self.assertAlmostEqual(blocks[2].get_t_sec(), 20.0)
            self.assertAlmostEqual(blocks[0].get_bitrate_kbps(), 12345.6)
            self.assertAlmostEqual(blocks[0].get_speed(), 1.2)
        finally:
            os.unlink(path)

    def test_parse_progress_file_missing_returns_empty(self):
        self.assertEqual(parse_progress_file("/no/such/file.log"), [])

    def test_bucket_blocks_by_window_keeps_latest_in_each_bucket(self):
        b1 = FfmpegProgressBlock()
        b1.add("out_time_us", str(int(2e6)))
        b1.add("frame", "10")
        b2 = FfmpegProgressBlock()
        b2.add("out_time_us", str(int(8e6)))
        b2.add("frame", "20")
        b3 = FfmpegProgressBlock()
        b3.add("out_time_us", str(int(11e6)))
        b3.add("frame", "30")
        # window=10 should put b1 and b2 in bucket 0, b3 in bucket 10.
        # The bucket-0 latest is b2.
        out = bucket_blocks_by_window([b1, b2, b3], 10)
        self.assertEqual(set(out.keys()), {0, 10})
        self.assertEqual(out[0].get_frame(), 20)
        self.assertEqual(out[10].get_frame(), 30)


class TestParseInterval(unittest.TestCase):
    def test_parse_perf_csv_well_formed(self):
        rows = (
            "1.001234,123,,task-clock,,1.00,100.00\n"
            "1.001234,5000,,cycles,,1.00,100.00\n"
            "2.001234,250,,task-clock,,1.00,100.00\n"
        )
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".csv") as f:
            f.write(rows)
            path = f.name
        try:
            p = VideoTranscodeParser(window_sec=2, perf_csv_path=path)
            buckets = p.parse_perf_csv()
            self.assertIn(0, buckets)
            self.assertIn(2, buckets)
            self.assertAlmostEqual(buckets[0]["task-clock"], 123.0)
            self.assertAlmostEqual(buckets[0]["cycles"], 5000.0)
            self.assertAlmostEqual(buckets[2]["task-clock"], 250.0)
        finally:
            os.unlink(path)

    def test_parse_perf_csv_missing_file(self):
        p = VideoTranscodeParser(window_sec=2, perf_csv_path="/no/such/path")
        self.assertEqual(p.parse_perf_csv(), {})


if __name__ == "__main__":
    unittest.main()
