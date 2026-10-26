// The test runner inserts the actual configure_active_listener() definition.
// Only its hardware side effects are stubbed; retry cancellation is real.
#include "delayed_tx_retry.h"
#include "proflame2_decoder.h"
#include "esphome/core/log.h"
#include <cassert>
#include <inttypes.h>
#include <string>
#include <vector>

namespace esphome {
namespace proflame2_tembed {
static const char* const TAG = "test";
struct Command {
  uint32_t generation{0};
  uint32_t bits{182};
};

class Proflame2TEmbedComponent {
public:
  void configure_active_listener(bool enabled, uint32_t serial_id, uint8_t c1, uint8_t d1, uint8_t c2, uint8_t d2);
  void cancel_delayed_retransmission_(const char* reason) {
    cancellations.emplace_back(reason);
    retry.cancel();
  }
  void set_fifo_capture_enabled(bool value) {
    fifo_enabled = value;
  }
  void set_rmt_pulse_capture_enabled_(bool value) {
    rx_rmt_pulse_capture_enabled_ = value;
  }
  uint32_t queue_command(uint32_t bits) {
    Command command{retry.next_generation(), bits};
    retry.accepted(command);
    return command.generation;
  }
  void enable_profile(const Proflame2DecodeProfile& profile) {
    configure_active_listener(true, profile.serial_id, profile.c1, profile.d1, profile.c2, profile.d2);
  }
  bool rx_active_listener_requested_{false};
  bool rx_active_listener_filter_configured_{false};
  bool rx_active_listener_use_rmt_{false};
  bool rx_rmt_pulse_capture_enabled_{false};
  bool fifo_enabled{false};
  Proflame2DecodeProfile rx_active_listener_profile_{};
  Proflame2DecodeProfile rx_active_listener_last_enabled_profile_{};
  DelayedTxRetry<Command> retry;
  std::vector<std::string> cancellations;
};

// INSERT_PRODUCTION_LISTENER_DEFINITION

} // namespace proflame2_tembed
} // namespace esphome

using namespace esphome::proflame2_tembed;

int main() {
  const Proflame2DecodeProfile profile{true, 0x3B3F02, 5, 7, 1, 8};
  for (bool rmt : {false, true}) {
    for (uint32_t bits : {182U, 260U}) {
      Proflame2TEmbedComponent controller;
      controller.rx_active_listener_use_rmt_ = rmt;
      controller.enable_profile(profile);
      controller.retry.set_enabled(true);
      controller.configure_active_listener(false, 0, 0, 0, 0, 0);
      assert(!controller.rx_active_listener_profile_.enabled);
      assert(controller.rx_active_listener_profile_.serial_id == 0);
      assert(!controller.rx_active_listener_filter_configured_);
      assert(!controller.rx_active_listener_requested_);
      assert(!controller.fifo_enabled && !controller.rx_rmt_pulse_capture_enabled_);

      // Observed HA flow: stop RX, send, complete, resume the SAME profile.
      auto token = controller.queue_command(bits);
      assert(controller.retry.complete(token, true, false, 1000));
      controller.cancellations.clear();
      controller.enable_profile(profile);
      assert(controller.cancellations.empty());
      assert(controller.retry.pending());
      assert(controller.rx_active_listener_requested_ && controller.rx_active_listener_filter_configured_);
      assert(rmt ? controller.rx_rmt_pulse_capture_enabled_ : controller.fifo_enabled);
      // Periodic policy refresh must not cancel or restart the timer either.
      controller.enable_profile(profile);
      Command repeated;
      assert(!controller.retry.take_due(2999, repeated));
      assert(controller.retry.take_due(3000, repeated) && repeated.bits == bits);
      assert(!controller.retry.take_due(10000, repeated));

      // The same profile can also resume before completion is consumed.
      controller.configure_active_listener(false, 0, 0, 0, 0, 0);
      token = controller.queue_command(bits);
      controller.enable_profile(profile);
      assert(controller.retry.complete(token, true, false, 2000));
      assert(controller.retry.take_due(4000, repeated));

      // An explicit stop AFTER a retry was armed still cancels it.
      token = controller.queue_command(bits);
      assert(controller.retry.complete(token, true, false, 1000));
      controller.configure_active_listener(false, 0, 0, 0, 0, 0);
      assert(!controller.retry.take_due(3000, repeated));
      controller.enable_profile(profile);
      assert(!controller.retry.pending());

      // A real remote-ID, integrity-profile, or learning-mode change cancels
      // both an armed retry and a not-yet-consumed original completion.
      for (int field = 0; field < 6; ++field) {
        auto different = profile;
        switch (field) {
        case 0:
          ++different.serial_id;
          break;
        case 1:
          ++different.c1;
          break;
        case 2:
          ++different.d1;
          break;
        case 3:
          ++different.c2;
          break;
        case 4:
          ++different.d2;
          break;
        case 5:
          different = Proflame2DecodeProfile{};
          break;
        }
        for (bool completed : {false, true}) {
          controller.enable_profile(profile);
          controller.configure_active_listener(false, 0, 0, 0, 0, 0);
          token = controller.queue_command(bits);
          if (completed) {
            assert(controller.retry.complete(token, true, false, 1000));
          }
          controller.cancellations.clear();
          controller.enable_profile(different);
          assert(controller.cancellations.size() == 1);
          assert(controller.cancellations.front() == "listener_profile_changed");
          assert(!controller.retry.complete(token, true, false, 1000));
          assert(!controller.retry.take_due(3000, repeated));
        }
      }
    }
  }
}
