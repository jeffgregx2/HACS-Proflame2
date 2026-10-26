#pragma once

#include <cstdint>

namespace esphome {
namespace proflame2_tembed {

// Main-loop connection edges only. Initial association is not a reconnection;
// an API reconnect without a Wi-Fi edge must not change these counters.
class WifiDiagnostics {
public:
  void observe(bool connected, uint32_t now_ms) {
    if (connected == connected_) {
      return;
    }
    connected_ = connected;
    if (connected) {
      if (ever_connected_) {
        ++reconnect_count_;
        last_outage_ms_ = now_ms - outage_started_ms_;
      }
      ever_connected_ = true;
    } else {
      ++disconnect_count_;
      outage_started_ms_ = now_ms;
    }
  }

  bool connected() const {
    return connected_;
  }
  uint32_t disconnect_count() const {
    return disconnect_count_;
  }
  uint32_t reconnect_count() const {
    return reconnect_count_;
  }
  uint32_t last_outage_ms() const {
    return last_outage_ms_;
  }

private:
  bool connected_{false};
  bool ever_connected_{false};
  uint32_t disconnect_count_{0};
  uint32_t reconnect_count_{0};
  uint32_t outage_started_ms_{0};
  uint32_t last_outage_ms_{0};
};

} // namespace proflame2_tembed
} // namespace esphome
