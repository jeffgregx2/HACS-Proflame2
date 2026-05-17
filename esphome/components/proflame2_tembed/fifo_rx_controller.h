// FIFO RX rolling-window storage for the Proflame2 T-Embed endpoint.
//
// This class owns raw CC1101 FIFO bytes and timing metadata only. It does not
// configure the radio, decode Proflame2 packets, publish HA events, or decide
// active-listener policy.

#pragma once

#include <array>
#include <cstddef>
#include <cstdint>

namespace esphome {
namespace proflame2_tembed {

struct FifoRxWindow {
  uint32_t complete_tick_ms{0};
  uint32_t window_start_tick_ms{0};
  uint32_t first_selected_tick_ms{0};
  uint32_t last_selected_tick_ms{0};
  uint32_t wall_clock_window_coverage_ms{0};
  uint32_t post_last_byte_quiet_ms{0};
  uint32_t first_byte_delta_from_window_start_ms{0};
  uint16_t selected_count{0};
  bool timing_available{false};
  bool enabled_long_enough{false};
  bool dropped_required_window_byte{false};
  bool trailing_window_complete{false};
};

class FifoRxController {
public:
  static constexpr size_t DRAIN_SCRATCH_BYTES = 64U;
  static constexpr size_t ROLLING_CAPACITY_BYTES = 4096U;

  void reset(uint32_t enable_tick_ms);
  void record_byte(uint8_t value, uint32_t tick_ms);
  FifoRxWindow select_window(uint32_t requested_complete_tick_ms, uint32_t export_window_ms);

  uint8_t* drain_scratch_data() {
    return this->drain_scratch_.data();
  }
  size_t drain_scratch_size() const {
    return this->drain_scratch_.size();
  }
  const uint8_t* selected_data() const {
    return this->selected_bytes_.data();
  }
  uint8_t* selected_data() {
    return this->selected_bytes_.data();
  }
  size_t selected_capacity() const {
    return this->selected_bytes_.size();
  }

  void increment_poll_count() {
    this->poll_count_++;
  }
  void update_rxbytes_max(uint8_t value);
  void set_rxbytes_final(uint8_t value) {
    this->rxbytes_final_ = value;
  }
  void set_radio_status(uint8_t marcstate, uint8_t rssi_raw, uint8_t lqi_raw, uint8_t pktstatus);
  void mark_hardware_overflow() {
    this->hardware_overflow_ = true;
  }
  void set_complete_tick(uint32_t complete_tick_ms) {
    this->capture_complete_tick_ms_ = complete_tick_ms;
  }
  void set_active_listener_last_scan(uint32_t tick_ms) {
    this->active_listener_last_scan_ms_ = tick_ms;
  }
  void set_status_last_log(uint32_t tick_ms) {
    this->status_last_log_ms_ = tick_ms;
  }

  bool rolling_overflow() const {
    return this->rolling_overflow_;
  }
  bool hardware_overflow() const {
    return this->hardware_overflow_;
  }
  uint16_t rolling_count() const {
    return this->rolling_count_;
  }
  uint32_t capture_enable_tick_ms() const {
    return this->capture_enable_tick_ms_;
  }
  uint32_t capture_complete_tick_ms() const {
    return this->capture_complete_tick_ms_;
  }
  uint32_t bytes_seen_since_enable() const {
    return this->bytes_seen_since_enable_;
  }
  uint32_t bytes_dropped_since_enable() const {
    return this->bytes_dropped_since_enable_;
  }
  uint32_t last_byte_tick_ms() const {
    return this->last_byte_tick_ms_;
  }
  uint32_t last_interesting_byte_tick_ms() const {
    return this->last_interesting_byte_tick_ms_;
  }
  uint32_t last_dropped_byte_tick_ms() const {
    return this->last_dropped_byte_tick_ms_;
  }
  uint32_t interesting_bytes_since_export() const {
    return this->interesting_bytes_since_export_;
  }
  uint32_t active_listener_last_scan_ms() const {
    return this->active_listener_last_scan_ms_;
  }
  uint32_t status_last_log_ms() const {
    return this->status_last_log_ms_;
  }
  uint32_t poll_count() const {
    return this->poll_count_;
  }
  uint8_t rxbytes_max() const {
    return this->rxbytes_max_;
  }
  uint8_t rxbytes_final() const {
    return this->rxbytes_final_;
  }
  uint8_t marcstate_last() const {
    return this->marcstate_last_;
  }
  uint8_t rssi_raw_last() const {
    return this->rssi_raw_last_;
  }
  uint8_t lqi_raw_last() const {
    return this->lqi_raw_last_;
  }
  uint8_t pktstatus_last() const {
    return this->pktstatus_last_;
  }

private:
  bool rolling_overflow_{false};
  bool hardware_overflow_{false};
  uint16_t rolling_write_at_{0};
  uint16_t rolling_count_{0};
  uint32_t capture_enable_tick_ms_{0};
  uint32_t capture_complete_tick_ms_{0};
  uint32_t bytes_seen_since_enable_{0};
  uint32_t bytes_dropped_since_enable_{0};
  uint32_t last_byte_tick_ms_{0};
  uint32_t last_interesting_byte_tick_ms_{0};
  uint32_t last_dropped_byte_tick_ms_{0};
  uint32_t interesting_bytes_since_export_{0};
  uint32_t active_listener_last_scan_ms_{0};
  uint32_t status_last_log_ms_{0};
  uint32_t poll_count_{0};
  uint8_t rxbytes_max_{0};
  uint8_t rxbytes_final_{0};
  uint8_t marcstate_last_{0};
  uint8_t rssi_raw_last_{0};
  uint8_t lqi_raw_last_{0};
  uint8_t pktstatus_last_{0};
  std::array<uint8_t, DRAIN_SCRATCH_BYTES> drain_scratch_{};
  std::array<uint8_t, ROLLING_CAPACITY_BYTES> rolling_bytes_{};
  std::array<uint32_t, ROLLING_CAPACITY_BYTES> rolling_byte_ticks_ms_{};
  std::array<uint8_t, ROLLING_CAPACITY_BYTES> selected_bytes_{};
};

} // namespace proflame2_tembed
} // namespace esphome
