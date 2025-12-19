#!/usr/bin/env python3
# pyre-strict
# Copyright (c) Meta Platforms, Inc. and affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

import argparse
import os
import pathlib
import subprocess
from typing import List, Optional

BENCHPRESS_ROOT: pathlib.Path = pathlib.Path(os.path.abspath(__file__)).parents[2]
UCACHE_BENCH_DIR: str = os.path.join(BENCHPRESS_ROOT, "benchmarks", "ucache_bench")

# Constants
MEM_USAGE_FACTOR = 0.75  # to prevent OOM


def run_cmd(
    cmd: List[str], timeout: Optional[int] = None, for_real: bool = True
) -> str:
    print(" ".join(cmd))
    if for_real:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        try:
            if timeout:
                proc.wait(timeout=timeout)
            else:
                proc.wait()
        except subprocess.TimeoutExpired:
            proc.terminate()
            proc.wait()
        stdout, _ = proc.communicate()
        return stdout.decode("utf-8")
    else:
        return ""


def run_server(args: argparse.Namespace) -> None:
    # Calculate number of threads
    n_cores = len(os.sched_getaffinity(0))
    n_threads = args.num_threads if args.num_threads > 0 else max(1, n_cores // 2)

    # Calculate memory size
    memory_mb = int(args.memsize * 1024 * MEM_USAGE_FACTOR)

    print(
        f"Starting UcacheBench server with {n_threads} threads and {memory_mb}MB memory"
    )

    server_binary = os.path.join(UCACHE_BENCH_DIR, "server", "ucachebench_server")
    server_cmd = [
        server_binary,
        f"--port={args.port}",
        f"--memory_mb={memory_mb}",
        f"--num_threads={n_threads}",
        f"--hash_power={args.hash_power}",
        f"--pool_name={args.pool_name}",
        f"--cache_mode={args.cache_mode}",
    ]

    # Add Navy-specific options for hybrid mode
    if args.cache_mode == "hybrid":
        server_cmd.extend(
            [
                f"--navy_cache_path={args.navy_cache_path}",
                f"--navy_cache_size_mb={args.navy_cache_size_mb}",
                f"--navy_block_size={args.navy_block_size}",
                f"--navy_device_max_write_rate={args.navy_device_max_write_rate}",
                f"--navy_region_size_mb={args.navy_region_size_mb}",
                f"--navy_clean_regions_pool={args.navy_clean_regions_pool}",
            ]
        )
        if args.navy_truncate_file:
            server_cmd.append("--navy_truncate_file=true")
        else:
            server_cmd.append("--navy_truncate_file=false")

    if args.verbose:
        server_cmd.append("--verbose")

    timeout = args.warmup_time + args.test_time + args.timeout_buffer
    stdout = run_cmd(server_cmd, timeout, args.real)
    print(stdout)


def run_client(args: argparse.Namespace) -> None:
    print("Starting UcacheBench client...")

    # Calculate number of threads
    n_cores = len(os.sched_getaffinity(0))
    n_threads = args.num_threads if args.num_threads > 0 else max(1, n_cores - 2)

    client_binary = os.path.join(UCACHE_BENCH_DIR, "client", "ucachebench_client")
    # Calculate number of proxy threads for connection management
    n_proxies = args.num_proxies if args.num_proxies > 0 else n_cores

    client_cmd = [
        client_binary,
        f"--server_host={args.server_hostname}",
        f"--server_port={args.server_port_number}",
        f"--num_threads={n_threads}",
        f"--num_proxies={n_proxies}",
        f"--duration_seconds={args.test_time}",
        f"--warmup_seconds={args.warmup_time}",
        f"--key_count={args.key_count}",
        f"--value_size_min={args.value_size_min}",
        f"--value_size_max={args.value_size_max}",
        f"--get_ratio={args.get_ratio}",
        f"--qps_target={args.qps_target}",
    ]

    if args.verbose:
        client_cmd.append("--verbose")

    stdout = run_cmd(client_cmd, timeout=None, for_real=args.real)
    print(stdout)


def init_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )

    # Sub-command parsers
    sub_parsers = parser.add_subparsers(help="Commands")
    server_parser = sub_parsers.add_parser(
        "server",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        help="run server",
    )
    client_parser = sub_parsers.add_parser(
        "client",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        help="run client",
    )

    # Server-side arguments
    server_parser.add_argument(
        "--memsize", type=float, required=True, help="memory size in GB, e.g. 1 or 2"
    )
    server_parser.add_argument("--port", type=int, default=11211, help="Server port")
    server_parser.add_argument(
        "--num-threads",
        type=int,
        default=0,
        help="Number of server threads (0 = auto-detect)",
    )
    server_parser.add_argument(
        "--hash-power",
        type=int,
        default=20,
        help="Hash table power for cachelib",
    )
    server_parser.add_argument(
        "--pool-name",
        type=str,
        default="default",
        help="Pool name for cachelib",
    )

    # Cache configuration arguments
    server_parser.add_argument(
        "--cache-mode",
        type=str,
        default="memory",
        choices=["memory", "hybrid"],
        help="Cache mode: 'memory' for RAM-only, 'hybrid' for RAM+SSD",
    )
    server_parser.add_argument(
        "--navy-cache-path",
        type=str,
        default="/tmp/ucachebench_ssd",
        help="Path for Navy cache files (hybrid mode only)",
    )
    server_parser.add_argument(
        "--navy-cache-size-mb",
        type=int,
        default=4096,
        help="Navy cache size in MB (hybrid mode only)",
    )
    server_parser.add_argument(
        "--navy-block-size",
        type=int,
        default=4096,
        help="Navy block size in bytes (hybrid mode only)",
    )
    server_parser.add_argument(
        "--navy-device-max-write-rate",
        type=int,
        default=0,
        help="Max Navy write rate MB/s, 0=unlimited (hybrid mode only)",
    )
    server_parser.add_argument(
        "--navy-region-size-mb",
        type=int,
        default=16,
        help="Navy region size in MB (hybrid mode only)",
    )
    server_parser.add_argument(
        "--navy-clean-regions-pool",
        type=int,
        default=4,
        help="Number of clean regions to maintain (hybrid mode only)",
    )
    server_parser.add_argument(
        "--navy-truncate-file",
        action="store_true",
        default=True,
        help="Truncate Navy cache file on startup (hybrid mode only)",
    )
    server_parser.add_argument(
        "--timeout-buffer",
        type=int,
        default=120,
        help="extra time the server will wait beyond warmup and test time, "
        + "in seconds, for the clients to start up",
    )
    server_parser.add_argument(
        "--warmup-time", type=int, default=10, help="warmup time in seconds"
    )
    server_parser.add_argument(
        "--test-time", type=int, default=60, help="test time in seconds"
    )
    server_parser.add_argument(
        "--verbose", action="store_true", help="Enable verbose logging"
    )
    server_parser.add_argument("--real", action="store_true", help="for real")

    # Client-side arguments
    client_parser.add_argument(
        "--server-hostname", type=str, required=True, help="Server hostname"
    )
    client_parser.add_argument(
        "--server-port-number", type=int, default=11211, help="Server port"
    )
    client_parser.add_argument(
        "--num-threads",
        type=int,
        default=0,
        help="Number of client threads (0 = auto-detect)",
    )
    client_parser.add_argument(
        "--num-proxies",
        type=int,
        default=0,
        help="Number of mcrouter proxy threads for connection pooling (0 = auto-detect). "
        "To simulate production-scale connections (e.g., 20K), increase this value. "
        "Each proxy thread establishes connections to the server as needed.",
    )
    client_parser.add_argument(
        "--connections-per-thread",
        type=int,
        default=10,
        help="[DEPRECATED - Not currently used] Number of connections per client thread",
    )
    client_parser.add_argument(
        "--key-count",
        type=int,
        default=100000,
        help="Number of unique keys in the key space",
    )
    client_parser.add_argument(
        "--value-size-min",
        type=int,
        default=64,
        help="Minimum value size in bytes",
    )
    client_parser.add_argument(
        "--value-size-max",
        type=int,
        default=1024,
        help="Maximum value size in bytes",
    )
    client_parser.add_argument(
        "--get-ratio",
        type=float,
        default=0.9,
        help="Ratio of GET operations (vs SET operations)",
    )
    client_parser.add_argument(
        "--qps-target",
        type=int,
        default=0,
        help="Target QPS (0 = unlimited)",
    )
    client_parser.add_argument(
        "--warmup-time", type=int, default=10, help="warmup time in seconds"
    )
    client_parser.add_argument(
        "--test-time", type=int, default=60, help="test time in seconds"
    )
    client_parser.add_argument(
        "--verbose", action="store_true", help="Enable verbose logging"
    )
    client_parser.add_argument("--real", action="store_true", help="for real")

    # Set default functions
    server_parser.set_defaults(func=run_server)
    client_parser.set_defaults(func=run_client)

    return parser


def main() -> None:
    parser = init_parser()
    args = parser.parse_args()

    # Ensure the benchmark binaries exist
    server_binary = os.path.join(UCACHE_BENCH_DIR, "server", "ucachebench_server")
    client_binary = os.path.join(UCACHE_BENCH_DIR, "client", "ucachebench_client")

    if not os.path.exists(server_binary):
        print(f"Error: Server binary not found at {server_binary}")
        print(
            "Please build the benchmark using: buck build //cea/chips/benchpress/benchmarks/ucache_bench/server:ucachebench_server"
        )
        exit(1)

    if not os.path.exists(client_binary):
        print(f"Error: Client binary not found at {client_binary}")
        print(
            "Please build the benchmark using: buck build //cea/chips/benchpress/benchmarks/ucache_bench/client:ucachebench_client"
        )
        exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
