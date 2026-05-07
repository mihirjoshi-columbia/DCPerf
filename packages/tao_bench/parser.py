#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.


class TaoBenchClientIntervalSnapshot:
    """Parses a single ``INTERVAL t=... ...`` line emitted by tao_bench_client
    when it's run with ``--window=<sec>``.

    The line is space-delimited ``key=value`` pairs after the ``INTERVAL``
    leader, so any reordering by the C++ side is tolerated.
    """

    KEYS = (
        "t",
        "set_qps",
        "get_qps",
        "hit_rate",
        "avg_us",
        "p50_us",
        "p99_us",
        "p999_us",
        "max_us",
    )

    def __init__(self, line):
        self.valid = False
        line = line.strip()
        if not line.startswith("INTERVAL"):
            return
        for token in line.split()[1:]:
            if "=" not in token:
                continue
            key, _, value = token.partition("=")
            key = key.strip()
            value = value.strip()
            if key not in self.KEYS:
                continue
            try:
                setattr(self, key, float(value))
            except ValueError:
                continue
        self.valid = all(hasattr(self, k) for k in self.KEYS)

    def get(self, key, default=0.0):
        return getattr(self, key, default)


class TaoBenchServerSnapshot:
    KEYS = ["fast_qps", "hit_rate", "slow_qps", "slow_qps_oom", "nanosleeps_per_sec"]

    def __init__(self, line):
        if line.strip().startswith("OUT OF MEMORY"):
            self.is_oom = True
            self.valid = True
        if not line.strip().startswith("fast_qps ="):
            if not hasattr(self, "valid"):
                self.valid = False
            return
        self.is_oom = False
        items = line.split(",")
        for keyvalue in items:
            try:
                key, value = keyvalue.split("=", maxsplit=2)
                key = key.strip()
                value = value.strip()
                if key in self.KEYS:
                    setattr(self, key, float(value))
            except ValueError:
                continue
        # all keys must be present in order to be a valid datapoint
        self.valid = True
        for key in self.KEYS:
            if not hasattr(self, key):
                self.valid = False
                break

    def get(self, key):
        if key in self.KEYS and not hasattr(self, key):
            return 0.0
        return getattr(self, key)


class TaoBenchParser:
    # Minimum hit rate for a server data point to be considered in
    # result qps calculation
    MIN_HIT_RATE = 0.88

    def __init__(
        self,
        server_csv_name="server.csv",
        window_sec=None,
        client_csv_name=None,
    ):
        self.server_csv_name = server_csv_name
        # When None we fall back to the historical 5s cadence; callers that
        # know --window can pass it explicitly so t_sec reflects real time.
        self.window_sec = (
            window_sec if window_sec and window_sec > 0 else self.DEFAULT_WINDOW_SEC
        )
        # Optional per-client time-series CSV; when None we skip emission.
        self.client_csv_name = client_csv_name
        # Populated by parse() so autoscale can re-aggregate without re-parsing.
        self.client_intervals = []

    def parse(self, stdout, stderr, returncode):
        """Extracts TAO bench results from stdout."""
        metrics = {"role": "unknown"}
        server_snapshots = []
        client_intervals = []
        warmup_done = False
        exec_done = False
        for line in stdout:
            items = line.split()
            # server metrics
            if line.strip().startswith("fast_qps =") or line.strip().startswith(
                "OUT OF MEMORY"
            ):
                metrics["role"] = "server"
                server_snapshots.append(TaoBenchServerSnapshot(line))
            # client per-window INTERVAL metrics (--window=N enabled)
            if line.strip().startswith("INTERVAL"):
                snap = TaoBenchClientIntervalSnapshot(line)
                if snap.valid:
                    client_intervals.append(snap)
                    if metrics["role"] == "unknown":
                        metrics["role"] = "client"
            # client metrics
            if line.strip().startswith("ALL STATS"):
                exec_done = warmup_done
                warmup_done = True
                metrics["role"] = "client"
            if exec_done:
                if line.strip().startswith("Sets"):
                    metrics["set_qps"] = float(items[1])
                elif line.strip().startswith("Gets"):
                    metrics["qps"] = float(items[1])
        # calcualte server-side QPS
        if metrics["role"] == "server":
            self.process_server_snapshots(metrics, server_snapshots)
            self.generate_server_csv(server_snapshots)
        # Stash for downstream callers (CSV writers, autoscale aggregator).
        self.client_intervals = client_intervals
        if client_intervals and self.client_csv_name:
            self.generate_client_csv(client_intervals)
        return metrics

    def generate_client_csv(self, client_intervals):
        """Write the parsed INTERVAL lines to a time-series CSV for plotting."""
        lines = ["t_sec,set_qps,get_qps,hit_rate,avg_us,p50_us,p99_us,p999_us,max_us\n"]
        for snap in client_intervals:
            lines.append(
                f"{snap.get('t')},{snap.get('set_qps')},{snap.get('get_qps')},"
                + f"{snap.get('hit_rate')},{snap.get('avg_us')},{snap.get('p50_us')},"
                + f"{snap.get('p99_us')},{snap.get('p999_us')},{snap.get('max_us')}\n"
            )
        with open(f"benchmarks/tao_bench/{self.client_csv_name}", "w") as table:
            table.writelines(lines)

    # Default cadence (seconds per row) when --window is not specified;
    # matches the historical default of --stats-interval=5000ms.
    DEFAULT_WINDOW_SEC = 5

    @staticmethod
    def format_server_row(seq, snapshot, window_sec=DEFAULT_WINDOW_SEC):
        fast_qps = snapshot.get("fast_qps")
        slow_qps = snapshot.get("slow_qps")
        is_oom = 1 if snapshot.is_oom else 0
        total_qps = fast_qps + slow_qps
        nanosleeps_per_sec = snapshot.get("nanosleeps_per_sec")
        t_sec = seq * window_sec
        return (
            f"{seq},{t_sec},{total_qps},{fast_qps},"
            + f"{snapshot.get('hit_rate')},{slow_qps},{is_oom},"
            + f"{snapshot.get('slow_qps_oom')},{nanosleeps_per_sec}\n"
        )

    def generate_server_csv(self, server_snapshots):
        lines = []
        lines.append(
            "seq,t_sec,total_qps,fast_qps,hit_rate,slow_qps,is_oom,slow_qps_oom,nanosleeps_per_sec\n"
        )

        seq = 0
        for snapshot in server_snapshots:
            lines.append(self.format_server_row(seq, snapshot, self.window_sec))
            seq += 1

        with open(f"benchmarks/tao_bench/{self.server_csv_name}", "w") as table:
            table.writelines(lines)

    def process_server_snapshots(self, metrics, server_snapshots):
        counter = 0
        total_fast_qps = 0
        total_slow_pqs = 0
        for snapshot in reversed(server_snapshots):
            if not snapshot.valid:
                continue
            # Also filter out data points with low hit rate
            if (
                snapshot.get("fast_qps") > 1
                and snapshot.get("hit_rate") >= self.MIN_HIT_RATE
            ):
                counter += 1
            else:
                continue
            total_fast_qps += snapshot.get("fast_qps")
            total_slow_pqs += snapshot.get("slow_qps")
            if counter >= 360 / 5 - 10:
                break
        if counter > 0:
            metrics["fast_qps"] = total_fast_qps / counter
            metrics["slow_qps"] = total_slow_pqs / counter
        else:
            metrics["fast_qps"] = 0
            metrics["slow_qps"] = 0
        metrics["total_qps"] = metrics["fast_qps"] + metrics["slow_qps"]
        if metrics["total_qps"] > 0:
            metrics["hit_ratio"] = metrics["fast_qps"] / metrics["total_qps"]
        else:
            metrics["hit_ratio"] = 0
        metrics["num_data_points"] = counter
