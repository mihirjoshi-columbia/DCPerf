// (c) Meta Platforms, Inc. and affiliates. Confidential and proprietary.

#pragma once

#include <functional>
#include <memory>
#include <thread>
#include <vector>

#include <folly/executors/IOThreadPoolExecutor.h>
#include <folly/io/async/AsyncTransport.h>
#include <folly/io/async/EventBase.h>
#include <thrift/lib/cpp2/async/AsyncProcessor.h>
#include <thrift/lib/cpp2/server/ThriftServer.h>

namespace facebook::ucachebench {

/**
 * Handles all client-facing RPC for the UcacheBench server.
 * Modeled after ucache/server's UcacheRpcServer structure.
 */
class UcacheBenchRpcServer {
 public:
  UcacheBenchRpcServer();

  ~UcacheBenchRpcServer();

  /**
   * Returns the number of IO threads we're configured with
   * (based on the constant rpc_io_threads, # of cpu cores, and the multiplier
   * settings).
   */
  static size_t numIoThreads() noexcept;

  /**
   * Creates a Thrift server using an IOThreadPool managed by this class.
   *
   * @returns a reference to the newly created server; the caller is
   *   free to customize the server options using the reference until
   *   the start() is called.
   */
  apache::thrift::ThriftServer& addThriftServer();

  /**
   * Start ThriftServer.
   *
   * @param threadInit Will be called on every IO thread once before starting
   *        the servers
   * @param threadCleanup Will be called on every IO thread once on destruction
   * of the servers.
   *
   * @throw std::invalid_argument on unexpected options/config
   * @throw std::logic_error on invalid config
   */
  void start(
      const std::function<void(folly::EventBase&)>& threadInit,
      const std::function<void()>& threadCleanup);

  /**
   * Shutdown ThriftServer.
   *
   * @throw std::system_error when error on shutdown from signal.
   */
  void stop();

  /**
   * Shutdown ThriftServer acceptors.
   * MT-safety: safe to call from any thread or concurrently. Calling this
   * function ensures acceptors are stopped when exits. Calling thread may be
   * blocked if other thread is also calling.
   */
  void ensureAcceptorsShutdown();

  /**
   * Return thread pool executor used by UcacheBenchRpcServer
   */
  std::shared_ptr<folly::IOThreadPoolExecutorBase> getThreadPoolExecutor();

  /**
   * Extracts Evb pointers from the IOThreadPool
   */
  std::vector<folly::EventBase*> extractIOEvbs();

 private:
  const size_t numThreads_{0};
  std::shared_ptr<folly::IOThreadPoolExecutorBase> ioThreadPool_;
  std::unique_ptr<apache::thrift::ThriftServer> thriftServer_;
  std::thread serverRunner_;
};

} // namespace facebook::ucachebench
