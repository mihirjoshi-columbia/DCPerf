<!--
Copyright (c) Meta Platforms, Inc. and affiliates.

This source code is licensed under the MIT license found in the
LICENSE file in the root directory of this source tree.
-->
# WDLBench

This is a benchmark that covers many widely distributed libraries and functions, which consume a considerable amount of CPU cycles in the datacenter.



## Usage
WDLBench and its job information are located in separate files. To use it (install, run, list, etc.), please **always** specify the path:
```
./benchpress_cli.py  -b wdl install|run|list|others ...
```


## Install WDLBench

```
./benchpress_cli.py  -b wdl install folly_single_core
```

## Run WDLBench
As of 2024 Q3, we have three libraries included -- `folly`, `lzbench`, and `openssl`,
each can run on a single core or all cores.

for `folly`, the user can choose to run them all together (i.e., run all microbenchmarks with one DCPerf run) or individually (i.e., one microbenchmark per DCPerf run) with different jobs.
```
./benchpress_cli.py  -b wdl run folly_single_core|folly_all_core|folly_multi_thread

./benchpress_cli.py  -b wdl run folly_individual -i '{"name": "function_name"}'
```
for list of functions to run then individually, see [list of benchmarks in folly](#list-of-benchmarks-in-folly).

For `lzbench` and `openssl`, the user can pass parameters to select how to run them.
```
./benchpress_cli.py  -b wdl run lzbench -i '{"type": "single_core|all_core"}'

./benchpress_cli.py  -b wdl run openssl -i '{"type": "single_core|all_core"}'
```
For `lzbench` and `openssl`, the user can also pass the `algo` parameter to specify the algorithm used, for `lzbench`, the default algorithm is `zstd`, while for `openssl`, the default algorithm is `ctr` (`aes-256-ctr`).

## benchmarks in folly

<table>
  <tr>
   <td>name </td>
   <td>Description</td>
   <td>catagories</td>
  </tr>
  <tr>
   <td>concurrency_concurrent_hash_map_benchmark</td>
   <td>multiple common operations of the folly::ConcurrentHashMap data structure</td>
   <td>multi_thread (locks, mutex, etc.)</td>
  </tr>
  <tr>
   <td>stats_digest_builder_benchmark </td>
   <td>append operations to a single DigestBuilder buffer from multiple threads</td>
   <td>multi_thread (locks, mutex, etc.)</td>
  </tr>
  <tr>
   <td> event_base_benchmark</td>
   <td>tests on and off speed of EventBase class, a wrapper of all async I/O processing functionalities </td>
   <td>single_core</td>
  </tr>
  <tr>
   <td>fibers_fibers_benchmark </td>
   <td> multiple common operations of FiberManager, which allows semi-parallel task execution on the same thread</td>
   <td>single_core</td>
  </tr>
  <tr>
   <td>function_benchmark </td>
   <td>evaluates function call performance</td>
   <td>single_core</td>
  </tr>
  <tr>
   <td>hash_hash_benchmark </td>
   <td>evaluates speed of three hash functions: SpookyHashV2, FNV64, and MurmurHash</td>
   <td>single_core</td>
  </tr>
  <tr>
   <td>hash_maps_bench </td>
   <td>multiple common operations of the F14 map data structure</td>
   <td>single_core</td>
  </tr>
  <tr>
   <td>iobuf_benchmark </td>
   <td>multiple common operations of IOBuf, which manages heap-allocated byte buffers.</td>
   <td>single_core</td>
  </tr>
  <tr>
   <td>lt_hash_benchmark</td>
   <td>evaluates speed of the lt hash function, which is common in crypto</td>
   <td>single_core, all_core</td>
  </tr>
  <tr>
   <td>memcpy_benchmark </td>
   <td>measures and compares memcpy from glibc and folly on vairous sizes </td>
   <td>single_core, all_core</td>
  </tr>
  <tr>
   <td>memset_benchmark </td>
   <td>measures and compares memset from glibc and folly on vairous sizes </td>
   <td>single_core, all_core</td>
  </tr>
  <tr>
   <td>random_benchmark </td>
   <td>evaluates speed of various random number generation functions </td>
   <td>single_core, all_core</td>
  </tr>
  <tr>
   <td>small_locks_benchmark </td>
   <td>evaluates performance of multi_thread locks, mutex, atomic operations, etc. </td>
   <td>multi_thread (locks, mutex, etc.)</td>
  </tr>
  <tr>
   <td>ProtocolBench </td>
   <td>evaluates performance of various thrift RPC protocol operations</td>
   <td>single_core, all_core</td>
  </tr>
</table>



## Reporting and Measurement
For now, for each benchmark, we report the results in the `out_name.json` file in `benchmark_metrics_<uuid>` folder. In the JSON file,
the keys are the items run in the benchmark, and the values are the corresponding performance
numbers (typically throughput (iterations per second)).

In future, we plan to add reference performance numbers of each benchmark as baseline, and DCPerf
can automatically compare the performance of your run against the default reference run.

## Per-kernel interval reporting (`--window`)

When `--window=<sec>` is set (default `0`, off), `run.sh` records a
START/END timestamp pair around every kernel invocation in
`interval_log.txt` and runs a Linux `perf stat -I window*1000 -x ,`
sidecar in the background, writing `perf_wdl.csv`.

Note that folly Benchmark kernels are too tight (typically a few hundred
microseconds per iteration) to instrument in-loop without an invasive
folly patch. So unlike `tao_bench`, `video_transcode_bench`, or
`feedsim`, the **interval unit is per-kernel rather than per-second**.
The output shape (interval CSV + perf-stat sidecar) is otherwise the
same.

After all kernels finish, `parser.py` writes `interval_metrics.csv`
with one row per kernel:

| col | meaning |
|---|---|
| `kernel` | the kernel name (e.g. `hash_benchmark`) |
| `start_t_sec` | suite-relative start time |
| `end_t_sec` | suite-relative end time |
| `duration_s` | end − start |
| `cycles`, `instructions`, … | average value of each perf event over the kernel's wall-clock window |

The final results file gains `total_kernels`, `mean_ipc`,
`mean_llc_miss_rate`. Default behavior is unchanged when `window=0`.

Example:

```
benchpress run folly_single_core -i window=1
```
