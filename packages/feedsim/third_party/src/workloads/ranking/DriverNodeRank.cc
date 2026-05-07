// Copyright (c) Meta Platforms, Inc. and affiliates.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

#include <chrono>
#include <cstdio>
#include <memory>
#include <set>
#include <string>
#include <vector>

#include "oldisim/ChildConnectionStats.h"
#include "oldisim/DriverNode.h"
#include "oldisim/Log.h"
#include "oldisim/LogHistogramSampler.h"
#include "oldisim/NodeThread.h"
#include "oldisim/ResponseContext.h"
#include "oldisim/TestDriver.h"
#include "oldisim/Util.h"

#include "DriverNodeRankCmdline.h"
#include "RequestTypes.h"

#include "utils.h"

static gengetopt_args_info args;

const int kMaxRequestSize = 8192;
const int kRecomputeQPSPeriod = 5;

struct ThreadData {
  std::string random_string;
  double qps_per_thread;
  uint64_t request_delay; // This is per thread
  oldisim::TestDriver *test_driver;
  event *recompute_qps_timer;
  // Per-window interval reporting, populated only on thread 0 when --window>0.
  event *interval_timer;
};

// Per-window INTERVAL line state. Lives in main thread / thread 0; never
// accessed concurrently because the libevent timer fires on its owning
// thread. Snapshots track the *cumulative* counts/histogram bins as of the
// last interval boundary so we can compute per-window deltas.
struct IntervalState {
  std::vector<ThreadData>* thread_data_ref = nullptr;
  uint64_t start_ts_ns = 0;
  uint64_t last_query_count = 0;
  std::vector<uint64_t> last_bins;
  double last_sum = 0.0;
  int window_sec = 0;
};
static IntervalState g_interval_state;

// Specific timer handler to recompute inter-request delays for QPS
void AddRecomputeDelayTimer(ThreadData &this_thread);
void RecomputeDelayTimerHandler(evutil_socket_t listener, int16_t flags,
                                void *arg);

// Declarations of handlers
void ThreadStartup(oldisim::NodeThread &thread,
                   oldisim::TestDriver &test_driver,
                   std::vector<ThreadData> &thread_data);
void MakeRequest(oldisim::NodeThread &thread, oldisim::TestDriver &test_driver,
                 std::vector<ThreadData> &thread_data);

void AddRecomputeDelayTimer(ThreadData &this_thread) {
  timeval t = {kRecomputeQPSPeriod, 0};
  evtimer_add(this_thread.recompute_qps_timer, &t);
}

void RecomputeDelayTimerHandler(evutil_socket_t listener, int16_t flags,
                                void *arg) {
  ThreadData *this_thread = reinterpret_cast<ThreadData *>(arg);
  const oldisim::ChildConnectionStats &stats =
      this_thread->test_driver->GetConnectionStats();

  // Get QPS for last stats period
  double qps = static_cast<double>(
                   stats.query_counts_.at(ranking::kPageRankRequestType)) /
               (stats.end_time_ - stats.start_time_) * 1000000000;

  // Adjust delay based on QPS
  this_thread->request_delay = (1000000 / this_thread->qps_per_thread) * (qps / this_thread->qps_per_thread);

  AddRecomputeDelayTimer(*this_thread);
}

// Forward declarations for interval reporting (defined further below to keep
// thread startup logic compact).
void AddIntervalTimer(ThreadData &thread_zero);
void IntervalTimerHandler(evutil_socket_t listener, int16_t flags, void *arg);

void ThreadStartup(oldisim::NodeThread &thread,
                   oldisim::TestDriver &test_driver,
                   std::vector<ThreadData> &thread_data) {
  ThreadData &this_thread = thread_data[thread.get_thread_num()];

  // Initialize random string with random bits
  this_thread.random_string = RandomString(kMaxRequestSize);

  // Store pointer to test_driver
  this_thread.test_driver = &test_driver;

  // If user gave QPS target, initialize QPS modulation
  if (args.qps_arg != 0) {
    this_thread.qps_per_thread =
        (static_cast<double>(args.qps_arg)) / args.threads_arg;
    this_thread.recompute_qps_timer = evtimer_new(
        thread.get_event_base(), RecomputeDelayTimerHandler, &this_thread);
    AddRecomputeDelayTimer(this_thread);
    this_thread.request_delay = 1000000 / this_thread.qps_per_thread;
  } else {
    this_thread.request_delay = 0;
  }

  // Per-window INTERVAL reporting. Install an event-loop timer only on
  // thread 0; the handler reads ChildConnectionStats from every thread's
  // TestDriver pointer (the thread_data vector is shared) and emits one
  // line per window with delta-derived QPS and HDR-histogram percentiles.
  if (thread.get_thread_num() == 0 && args.window_arg > 0) {
    g_interval_state.thread_data_ref = &thread_data;
    g_interval_state.start_ts_ns = oldisim::GetTimeAccurateNano();
    g_interval_state.last_query_count = 0;
    g_interval_state.last_sum = 0.0;
    g_interval_state.window_sec = args.window_arg;
    this_thread.interval_timer = evtimer_new(
        thread.get_event_base(), IntervalTimerHandler, &this_thread);
    AddIntervalTimer(this_thread);
  }
}

void AddIntervalTimer(ThreadData &thread_zero) {
  timeval t = {g_interval_state.window_sec, 0};
  evtimer_add(thread_zero.interval_timer, &t);
}

void IntervalTimerHandler(evutil_socket_t /*listener*/, int16_t /*flags*/,
                          void *arg) {
  ThreadData *thread_zero = reinterpret_cast<ThreadData *>(arg);
  if (!g_interval_state.thread_data_ref) {
    AddIntervalTimer(*thread_zero);
    return;
  }

  // Aggregate cumulative-since-start counts + histogram bins across all
  // threads. ChildConnectionStats here is *cumulative* per thread (the
  // existing recompute-delay path also reads it cumulative); we never call
  // Reset() on it to avoid disturbing that path.
  std::set<uint32_t> qt = {ranking::kPageRankRequestType};
  oldisim::ChildConnectionStats agg(qt);
  for (ThreadData &td : *g_interval_state.thread_data_ref) {
    if (td.test_driver != nullptr) {
      agg.Accumulate(td.test_driver->GetConnectionStats());
    }
  }

  uint64_t cur_count = agg.query_counts_.at(ranking::kPageRankRequestType);
  const oldisim::LogHistogramSampler &cur_hist =
      agg.query_samplers_.at(ranking::kPageRankRequestType);
  double cur_sum = cur_hist.sum_;

  uint64_t delta_count =
      cur_count >= g_interval_state.last_query_count
          ? cur_count - g_interval_state.last_query_count
          : 0;
  double delta_sum = cur_sum - g_interval_state.last_sum;

  // Build a delta histogram by subtracting last bins from cur bins.
  oldisim::LogHistogramSampler delta_hist(
      static_cast<int>(cur_hist.bins_.size()) - 1);
  if (g_interval_state.last_bins.size() == cur_hist.bins_.size()) {
    for (size_t i = 0; i < cur_hist.bins_.size(); i++) {
      uint64_t cur_b = cur_hist.bins_[i];
      uint64_t last_b = g_interval_state.last_bins[i];
      delta_hist.bins_[i] = cur_b >= last_b ? cur_b - last_b : 0;
    }
  } else {
    delta_hist.bins_ = cur_hist.bins_;  // First window — use cur as the delta.
  }
  delta_hist.sum_ = delta_sum;

  uint64_t now_ns = oldisim::GetTimeAccurateNano();
  double t_sec = (now_ns - g_interval_state.start_ts_ns) / 1.0e9;
  double window_sec = static_cast<double>(g_interval_state.window_sec);
  double qps = window_sec > 0 ? static_cast<double>(delta_count) / window_sec : 0.0;

  // LogHistogramSampler stores raw values as fed by sample(); oldisim feeds
  // it nanoseconds (originating_request.Time()), so percentiles are in ns.
  double avg_us = delta_count > 0 ? (delta_sum / delta_count) / 1000.0 : 0.0;
  double p50_us = delta_count > 0 ? delta_hist.get_nth(50.0) / 1000.0 : 0.0;
  double p95_us = delta_count > 0 ? delta_hist.get_nth(95.0) / 1000.0 : 0.0;
  double p99_us = delta_count > 0 ? delta_hist.get_nth(99.0) / 1000.0 : 0.0;

  std::fprintf(stdout,
               "INTERVAL t=%.3f qps=%.2f avg_us=%.2f p50_us=%.2f "
               "p95_us=%.2f p99_us=%.2f\n",
               t_sec, qps, avg_us, p50_us, p95_us, p99_us);
  std::fflush(stdout);

  g_interval_state.last_query_count = cur_count;
  g_interval_state.last_bins = cur_hist.bins_;
  g_interval_state.last_sum = cur_sum;

  AddIntervalTimer(*thread_zero);
}

void MakeRequest(oldisim::NodeThread &thread, oldisim::TestDriver &test_driver,
                 std::vector<ThreadData> &thread_data) {
  ThreadData &this_thread = thread_data[thread.get_thread_num()];

  test_driver.SendRequest(ranking::kPageRankRequestType,
                          this_thread.random_string.c_str(), 3000,
                          this_thread.request_delay);
}

int main(int argc, char **argv) {
  // Parse arguments
  if (cmdline_parser(argc, argv, &args) != 0) {
    DIE("cmdline_parser failed");
  }

  // Set logging level
  for (unsigned int i = 0; i < args.verbose_given; i++) {
    log_level = (log_level_t)(static_cast<int>(log_level) - 1);
  }
  if (args.quiet_given) {
    log_level = QUIET;
  }

  // Check requried arguments
  if (!args.server_given) {
    DIE("--server must be specified.");
  }

  auto host_port = ranking::utils::parseHostnameAndPort(args.server_arg);

  // Make storage for thread variables
  std::vector<ThreadData> thread_data(args.threads_arg);

  oldisim::DriverNode driver_node(host_port.first, host_port.second);

  driver_node.SetThreadStartupCallback(
      std::bind(ThreadStartup, std::placeholders::_1, std::placeholders::_2,
                std::ref(thread_data)));
  driver_node.SetMakeRequestCallback(
      std::bind(MakeRequest, std::placeholders::_1, std::placeholders::_2,
                std::ref(thread_data)));
  driver_node.RegisterRequestType(ranking::kPageRankRequestType);

  // Enable remote monitoring
  driver_node.EnableMonitoring(args.monitor_port_arg);

  driver_node.Run(args.threads_arg, args.affinity_given, args.connections_arg,
                  args.depth_arg);

  return 0;
}
