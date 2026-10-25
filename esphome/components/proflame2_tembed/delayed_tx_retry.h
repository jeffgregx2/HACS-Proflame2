// Main-task-only, one-shot retry policy. Payload ownership stays with the
// scheduler; the worker receives value copies through the existing queue.
#pragma once

#include <cstdint>

namespace esphome {
namespace proflame2_tembed {

template <typename Command> class DelayedTxRetry {
public:
  static constexpr uint32_t DELAY_MS = 2000;
  bool enabled() const {
    return enabled_;
  }
  bool pending() const {
    return pending_ || awaiting_completion_;
  }
  uint32_t next_generation() const {
    return generation_ + 1;
  }
  void set_enabled(bool value) {
    if (enabled_ != value) {
      cancel();
      enabled_ = value;
    }
  }
  void cancel() {
    ++generation_;
    pending_ = false;
    awaiting_completion_ = false;
  }
  // Call only after queue acceptance, never for a rejected or retry command.
  void accepted(const Command& command) {
    cancel();
    if (enabled_) {
      command_ = command;
      awaiting_completion_ = true;
    }
  }
  bool complete(uint32_t generation, bool ok, bool is_retry, uint32_t finished_ms) {
    if (!enabled_ || !awaiting_completion_ || generation != generation_ || is_retry) {
      return false;
    }
    awaiting_completion_ = false;
    pending_ = ok;
    finished_ms_ = finished_ms;
    return pending_;
  }
  // Consume before queueing, so a failed queue send cannot create a retry loop.
  bool take_due(uint32_t now_ms, Command& command) {
    if (!pending_ || static_cast<uint32_t>(now_ms - finished_ms_) < DELAY_MS) {
      return false;
    }
    command = command_;
    pending_ = false;
    return true;
  }

private:
  bool enabled_{false};
  bool pending_{false};
  bool awaiting_completion_{false};
  uint32_t generation_{0};
  uint32_t finished_ms_{0};
  Command command_{};
};

} // namespace proflame2_tembed
} // namespace esphome
