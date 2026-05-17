#include "fifo_rx_controller.h"

#include <algorithm>

namespace esphome {
namespace proflame2_tembed {

void FifoRxController::reset(uint32_t enable_tick_ms) {
  this->rolling_overflow_ = false;
  this->hardware_overflow_ = false;
  this->rolling_write_at_ = 0U;
  this->rolling_count_ = 0U;
  this->capture_enable_tick_ms_ = enable_tick_ms;
  this->capture_complete_tick_ms_ = 0U;
  this->bytes_seen_since_enable_ = 0U;
  this->bytes_dropped_since_enable_ = 0U;
  this->last_byte_tick_ms_ = 0U;
  this->last_interesting_byte_tick_ms_ = 0U;
  this->last_dropped_byte_tick_ms_ = 0U;
  this->interesting_bytes_since_export_ = 0U;
  this->active_listener_last_scan_ms_ = 0U;
  this->status_last_log_ms_ = 0U;
  this->poll_count_ = 0U;
  this->rxbytes_max_ = 0U;
  this->rxbytes_final_ = 0U;
  this->rolling_bytes_.fill(0U);
  this->rolling_byte_ticks_ms_.fill(0U);
}

void FifoRxController::record_byte(uint8_t value, uint32_t tick_ms) {
  if (this->rolling_count_ >= this->rolling_bytes_.size()) {
    this->rolling_overflow_ = true;
    this->bytes_dropped_since_enable_++;
    this->last_dropped_byte_tick_ms_ = this->rolling_byte_ticks_ms_[this->rolling_write_at_];
  } else {
    this->rolling_count_++;
  }

  this->rolling_bytes_[this->rolling_write_at_] = value;
  this->rolling_byte_ticks_ms_[this->rolling_write_at_] = tick_ms;
  this->rolling_write_at_ = static_cast<uint16_t>((this->rolling_write_at_ + 1U) % this->rolling_bytes_.size());
  this->bytes_seen_since_enable_++;
  this->last_byte_tick_ms_ = tick_ms;
  if (value != 0x00U && value != 0xFFU) {
    this->last_interesting_byte_tick_ms_ = tick_ms;
    this->interesting_bytes_since_export_++;
  }
}

FifoRxWindow FifoRxController::select_window(uint32_t requested_complete_tick_ms, uint32_t export_window_ms) {
  FifoRxWindow window;
  window.complete_tick_ms =
      this->capture_complete_tick_ms_ == 0U ? requested_complete_tick_ms : this->capture_complete_tick_ms_;
  window.window_start_tick_ms =
      window.complete_tick_ms >= export_window_ms ? window.complete_tick_ms - export_window_ms : 0U;

  const uint16_t capacity = static_cast<uint16_t>(this->rolling_bytes_.size());
  const uint16_t oldest = this->rolling_count_ < capacity ? 0U : this->rolling_write_at_;

  for (uint16_t logical = 0U; logical < this->rolling_count_; logical++) {
    const uint16_t ring_index = static_cast<uint16_t>((oldest + logical) % capacity);
    const uint32_t tick_ms = this->rolling_byte_ticks_ms_[ring_index];
    if (tick_ms < window.window_start_tick_ms || tick_ms > window.complete_tick_ms) {
      continue;
    }
    if (window.selected_count == 0U) {
      window.first_selected_tick_ms = tick_ms;
    }
    window.last_selected_tick_ms = tick_ms;
    if (window.selected_count < this->selected_bytes_.size()) {
      this->selected_bytes_[window.selected_count] = this->rolling_bytes_[ring_index];
    }
    window.selected_count++;
  }

  window.timing_available =
      this->capture_enable_tick_ms_ != 0U && window.complete_tick_ms >= this->capture_enable_tick_ms_;
  window.enabled_long_enough =
      window.timing_available && window.complete_tick_ms - this->capture_enable_tick_ms_ >= export_window_ms;
  window.dropped_required_window_byte =
      this->rolling_overflow_ && this->last_dropped_byte_tick_ms_ >= window.window_start_tick_ms;
  window.trailing_window_complete = window.timing_available && window.enabled_long_enough &&
                                    !window.dropped_required_window_byte && !this->hardware_overflow_;
  window.post_last_byte_quiet_ms = window.selected_count > 0U && window.complete_tick_ms >= window.last_selected_tick_ms
                                       ? window.complete_tick_ms - window.last_selected_tick_ms
                                       : 0U;
  window.first_byte_delta_from_window_start_ms =
      window.selected_count > 0U && window.first_selected_tick_ms >= window.window_start_tick_ms
          ? window.first_selected_tick_ms - window.window_start_tick_ms
          : 0U;
  window.wall_clock_window_coverage_ms =
      window.timing_available
          ? std::min<uint32_t>(window.complete_tick_ms - this->capture_enable_tick_ms_, export_window_ms)
          : 0U;
  return window;
}

void FifoRxController::update_rxbytes_max(uint8_t value) {
  this->rxbytes_max_ = std::max(this->rxbytes_max_, value);
}

void FifoRxController::set_radio_status(uint8_t marcstate, uint8_t rssi_raw, uint8_t lqi_raw, uint8_t pktstatus) {
  this->marcstate_last_ = marcstate;
  this->rssi_raw_last_ = rssi_raw;
  this->lqi_raw_last_ = lqi_raw;
  this->pktstatus_last_ = pktstatus;
}

} // namespace proflame2_tembed
} // namespace esphome
