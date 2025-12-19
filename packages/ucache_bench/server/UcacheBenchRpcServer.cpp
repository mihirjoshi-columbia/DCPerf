// (c) Meta Platforms, Inc. and affiliates. Confidential and proprietary.

#include "UcacheBenchRpcServer.h"

#include <folly/executors/IOThreadPoolExecutor.h>
#include <folly/executors/thread_factory/NamedThreadFactory.h>
#include <folly/portability/GFlags.h>
#include <thrift/lib/cpp2/server/ThriftServer.h>
#include "UcacheBenchIOThreadContext.h"

DEFINE_uint32(rpc_io_threads, 0, "Number of IO threads for RPC server");

namespace facebook::ucachebench {

namespace {
constexpr size_t kDefaultNumThreads = 4;
}

UcacheBenchRpcServer::UcacheBenchRpcServer() : numThreads_(numIoThreads()) {}

UcacheBenchRpcServer::~UcacheBenchRpcServer() {
  stop();
}

size_t UcacheBenchRpcServer::numIoThreads() noexcept {
  if (FLAGS_rpc_io_threads > 0) {
    return FLAGS_rpc_io_threads;
  }

  // Match production: use all CPU cores, no cap
  auto numCores = std::thread::hardware_concurrency();
  if (numCores > 0) {
    return numCores;
  }

  return kDefaultNumThreads;
}

apache::thrift::ThriftServer& UcacheBenchRpcServer::addThriftServer() {
  if (thriftServer_) {
    throw std::logic_error("ThriftServer already created");
  }

  thriftServer_ = std::make_unique<apache::thrift::ThriftServer>();
  return *thriftServer_;
}

void UcacheBenchRpcServer::start(
    const std::function<void(folly::EventBase&)>& threadInit,
    const std::function<void()>& threadCleanup) {
  if (!thriftServer_) {
    throw std::logic_error("No ThriftServer created");
  }

  // Create IO thread pool
  ioThreadPool_ = std::make_shared<folly::IOThreadPoolExecutor>(
      numThreads_,
      std::make_shared<folly::NamedThreadFactory>("UcacheBenchIO"));

  // Set up thread initialization with fiber context
  class CustomIOObserver : public folly::IOThreadPoolExecutorBase::IOObserver {
   public:
    CustomIOObserver(
        std::function<void(folly::EventBase&)> init,
        std::function<void()> cleanup)
        : threadInit_(std::move(init)), threadCleanup_(std::move(cleanup)) {}

    void registerEventBase(folly::EventBase& evb) override {
      // Initialize fiber context for this thread
      UcacheBenchIOThreadContext::init(evb);

      if (threadInit_) {
        threadInit_(evb);
      }
    }

    void unregisterEventBase(folly::EventBase& evb) override {
      if (threadCleanup_) {
        threadCleanup_();
      }
    }

   private:
    std::function<void(folly::EventBase&)> threadInit_;
    std::function<void()> threadCleanup_;
  };

  ioThreadPool_->addObserver(
      std::make_shared<CustomIOObserver>(threadInit, threadCleanup));

  thriftServer_->setIOThreadPool(ioThreadPool_);
  thriftServer_->setNumIOWorkerThreads(numThreads_);

  // Start the server in a separate thread
  serverRunner_ = std::thread([this]() { thriftServer_->serve(); });
}

void UcacheBenchRpcServer::stop() {
  if (thriftServer_) {
    thriftServer_->stop();
  }

  if (serverRunner_.joinable()) {
    serverRunner_.join();
  }

  if (ioThreadPool_) {
    ioThreadPool_->stop();
    ioThreadPool_.reset();
  }
}

void UcacheBenchRpcServer::ensureAcceptorsShutdown() {
  if (thriftServer_) {
    thriftServer_->stopListening();
  }
}

std::shared_ptr<folly::IOThreadPoolExecutorBase>
UcacheBenchRpcServer::getThreadPoolExecutor() {
  return ioThreadPool_;
}

std::vector<folly::EventBase*> UcacheBenchRpcServer::extractIOEvbs() {
  std::vector<folly::EventBase*> evbs;
  if (ioThreadPool_) {
    for (size_t i = 0; i < numThreads_; ++i) {
      if (auto evb = ioThreadPool_->getEventBase()) {
        evbs.push_back(evb);
      }
    }
  }
  return evbs;
}

} // namespace facebook::ucachebench
