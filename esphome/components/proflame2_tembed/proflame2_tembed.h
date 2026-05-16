// ESPHome external component for the LilyGO T-Embed CC1101 endpoint.
//
// This firmware remains a transport/display endpoint. Home Assistant owns
// profile/state authority and supplies prepared TX payloads. Active listening
// may use HA-provided profile constants to filter FIFO RX windows before
// publishing packet events. The firmware must not become a second source of
// fireplace state truth.

#pragma once

#ifndef PROFLAME2_TEMBED_DEBUG
#define PROFLAME2_TEMBED_DEBUG 0
#endif

#include <array>
#include <cstdint>
#include <string>
#include <vector>

#include "esphome/core/automation.h"
#include "esphome/core/component.h"
#include "esphome/core/gpio.h"
#include "esphome/components/api/custom_api_device.h"
#include "esphome/components/i2c/i2c.h"
#include "active_listener.h"
#include "battery_monitor.h"
#include "display_controller.h"
#include "display_state.h"
#include "fifo_rx_controller.h"
#include "proflame2_decoder.h"
#include "radio_cc1101.h"
#include "telemetry_publisher.h"
#include "tx_controller.h"

namespace esphome {
namespace sensor {
class Sensor;
}
namespace text_sensor {
class TextSensor;
}
namespace proflame2_tembed {

enum class RadioRuntimeStartReason : uint8_t {
  // Runtime is needed for HA-requested transmit or test-pattern work.
  TX_REQUEST = 0,
  // Runtime is needed only to collect or print diagnostic radio state.
  DIAGNOSTIC = 2,
};

enum class RadioRuntimeState : uint8_t {
  IDLE = 0,
  TX_ACTIVE = 1,
  ERROR = 3,
};

enum class RadioRuntimeInitState : uint8_t {
  UNINITIALIZED = 0,
  STARTING = 1,
  READY = 2,
  FAILED = 3,
};

/// ESPHome-facing Proflame2 endpoint shell for the T-Embed CC1101 board.
///
/// Ownership model:
/// - ESPHome owns this component instance and calls `setup()`, `loop()`, and
///   the generated service/action entrypoints on the main loop.
/// - Home Assistant owns semantic profile/state authority and sends prepared
///   TX bit payloads plus learned-profile constants.
/// - This component owns board IO, display state, CC1101 runtime state, FIFO
///   RX buffers, active-listener filtering, and telemetry publication.
///
/// RF invariants:
/// - TX has priority over RX. Any active FIFO listener is paused for TX and
///   restored afterward if it was previously requested.
/// - Active listening publishes only decoded packets matching the configured
///   learned serial/C/D profile. Nonmatching or undecodable FIFO windows are
///   diagnostic counters/snapshots, not semantic HA state.
/// - `PROFLAME2_TEMBED_DEBUG` gates deep manual FIFO diagnostics and raw-byte
///   dump controls. Production builds keep the FIFO semantic RX and TX paths.
///
/// This class is intentionally the ESPHome integration shell. Domain mechanics
/// live in small helpers:
/// - `BatteryMonitor` owns PMIC/battery reads.
/// - `DisplayController` owns display text/policy formatting.
/// - `TelemetryPublisher` owns publish-if-changed helpers.
/// - `FifoRxController` owns bounded FIFO byte-window storage.
/// - `ActiveListenerController` owns learned-profile packet acceptance policy.
/// - `TxController` owns TX payload validation and transport-shape policy.
///
/// The shell keeps cross-domain coordination: public HA/YAML entrypoints,
/// CC1101 runtime ownership, TX-over-RX priority, display/telemetry mutation,
/// and HA event publication.
class Proflame2TEmbedComponent : public Component,
                                 public RadioCC1101,
                                 public i2c::I2CDevice,
                                 public api::CustomAPIDevice {
public:
  void setup() override;
  void loop() override;
  void dump_config() override;

  void set_tx_frequency_hz(uint32_t value) {
    this->tx_frequency_hz_ = value;
  }
  void set_rx_frequency_hz(uint32_t value) {
    this->rx_frequency_hz_ = value;
  }
  void set_data_rate_bps(uint32_t value) {
    this->data_rate_bps_ = value;
  }
  void set_tx_repeat_count(uint8_t value) {
    this->tx_repeat_count_ = value;
  }
  void set_inter_frame_gap_us(uint32_t value) {
    this->inter_frame_gap_us_ = value;
  }
  void set_post_frame_idle_gap_us(uint32_t value) {
    this->post_frame_idle_gap_us_ = value;
  }
  void set_tx_mode(TXMode value) {
    this->tx_mode_ = value;
  }
  void set_tx_mode_requested(const std::string& value) {
    this->tx_mode_requested_ = value;
  }
  void set_native_group_timing_profile(NativeGroupTimingProfile value) {
    this->native_group_timing_profile_ = value;
  }
  void set_native_group_timing_profile_requested(const std::string& value) {
    this->native_group_timing_profile_requested_ = value;
  }
  void set_native_group_repeat_boundary_mode(NativeGroupRepeatBoundaryMode value) {
    this->native_group_repeat_boundary_mode_ = value;
  }
  void set_native_group_repeat_boundary_mode_requested(const std::string& value) {
    this->native_group_repeat_boundary_mode_requested_ = value;
  }
  void set_pre_burst_low_us(uint32_t value) {
    this->pre_burst_low_us_ = value;
  }
  void set_pre_frame_low_us(uint32_t value) {
    this->pre_frame_low_us_ = value;
  }
  void set_diagnostic_repeat_count_override(uint8_t value) {
    this->diagnostic_repeat_count_override_ = value;
  }
  void set_payload_bit_length_override(uint32_t value) {
    this->payload_bit_length_override_ = value;
  }
  void set_display_debug_mode(bool value) {
    this->display_.display_debug_mode = value;
    this->display_.display_refresh_pending = true;
  }
  void set_display_dim_timeout_min(uint32_t value) {
    this->display_dim_timeout_ms_ = value * 60000U;
  }
  void set_display_wake_on_activity(bool value) {
    this->display_wake_on_activity_ = value;
  }
  void set_display_dim_level(uint8_t value);
  void set_async_tx_data_pin(AsyncTxDataPin value) {
    this->async_tx_data_pin_ = value;
    RadioCC1101::set_async_tx_data_pin(value);
  }

  // Board/radio/display wiring setters called from the ESPHome codegen layer.
  // Pointers are borrowed from ESPHome and must not be deleted by this class.
  void set_board_power_enable_pin(GPIOPin* pin) {
    this->board_power_enable_pin_ = pin;
  }
  void set_cc1101_gdo0_pin(GPIOPin* pin) {
    this->cc1101_gdo0_pin_ = pin;
  }
  void set_cc1101_gdo2_pin(GPIOPin* pin) {
    this->cc1101_gdo2_pin_ = pin;
  }
  void set_rf_switch_sw1_pin(GPIOPin* pin) {
    this->rf_switch_sw1_pin_ = pin;
  }
  void set_rf_switch_sw0_pin(GPIOPin* pin) {
    this->rf_switch_sw0_pin_ = pin;
  }
  void set_endpoint_status_sensor(text_sensor::TextSensor* sensor) {
    this->endpoint_status_sensor_ = sensor;
  }
  void set_last_error_sensor(text_sensor::TextSensor* sensor) {
    this->last_error_sensor_ = sensor;
  }
  void set_last_tx_result_sensor(text_sensor::TextSensor* sensor) {
    this->last_tx_result_sensor_ = sensor;
  }
  void set_last_request_id_sensor(text_sensor::TextSensor* sensor) {
    this->last_request_id_sensor_ = sensor;
  }
  void set_last_tx_path_sensor(text_sensor::TextSensor* sensor) {
    this->last_tx_path_sensor_ = sensor;
  }
  void set_last_payload_hex_sensor(text_sensor::TextSensor* sensor) {
    this->last_payload_hex_sensor_ = sensor;
  }
  void set_last_marcstate_before_tx_sensor(text_sensor::TextSensor* sensor) {
    this->last_marcstate_before_tx_sensor_ = sensor;
  }
  void set_last_marcstate_after_tx_sensor(text_sensor::TextSensor* sensor) {
    this->last_marcstate_after_tx_sensor_ = sensor;
  }
  void set_cc1101_partnum_sensor(text_sensor::TextSensor* sensor) {
    this->cc1101_partnum_sensor_ = sensor;
  }
  void set_cc1101_version_sensor(text_sensor::TextSensor* sensor) {
    this->cc1101_version_sensor_ = sensor;
  }
  void set_tx_success_count_sensor(sensor::Sensor* sensor) {
    this->tx_success_count_sensor_ = sensor;
  }
  void set_tx_failure_count_sensor(sensor::Sensor* sensor) {
    this->tx_failure_count_sensor_ = sensor;
  }
  void set_last_payload_length_sensor(sensor::Sensor* sensor) {
    this->last_payload_length_sensor_ = sensor;
  }
  void set_last_request_repeat_count_sensor(sensor::Sensor* sensor) {
    this->last_request_repeat_count_sensor_ = sensor;
  }
  void set_last_tx_elapsed_ms_sensor(sensor::Sensor* sensor) {
    this->last_tx_elapsed_ms_sensor_ = sensor;
  }
  void set_tx_repeat_count_sensor(sensor::Sensor* sensor) {
    this->tx_repeat_count_sensor_ = sensor;
  }
  void set_firmware_protocol_version_sensor(sensor::Sensor* sensor) {
    this->firmware_protocol_version_sensor_ = sensor;
  }
  void set_config_revision_sensor(sensor::Sensor* sensor) {
    this->config_revision_sensor_ = sensor;
  }
  void set_battery_percent_sensor(sensor::Sensor* sensor) {
    this->battery_percent_sensor_ = sensor;
  }
  void set_rx_dropped_packet_count_sensor(sensor::Sensor* sensor) {
    this->rx_dropped_packet_count_sensor_ = sensor;
  }
  void set_rx_no_rf_capture_count_sensor(sensor::Sensor* sensor) {
    this->rx_no_rf_capture_count_sensor_ = sensor;
  }
  void set_rx_incomplete_fifo_count_sensor(sensor::Sensor* sensor) {
    this->rx_incomplete_fifo_count_sensor_ = sensor;
  }
  void set_rx_decode_failed_count_sensor(sensor::Sensor* sensor) {
    this->rx_decode_failed_count_sensor_ = sensor;
  }
  void set_rx_profile_mismatch_count_sensor(sensor::Sensor* sensor) {
    this->rx_profile_mismatch_count_sensor_ = sensor;
  }
  void set_rx_accepted_packet_count_sensor(sensor::Sensor* sensor) {
    this->rx_accepted_packet_count_sensor_ = sensor;
  }
  void set_rx_tx_suppressed_count_sensor(sensor::Sensor* sensor) {
    this->rx_tx_suppressed_count_sensor_ = sensor;
  }
  void set_rx_transport_unavailable_count_sensor(sensor::Sensor* sensor) {
    this->rx_transport_unavailable_count_sensor_ = sensor;
  }
  void set_rx_last_rejection_snapshot_sensor(text_sensor::TextSensor* sensor) {
    this->rx_last_rejection_snapshot_sensor_ = sensor;
  }

  /// HA service/action entrypoint for transmitting one prepared Proflame2 payload.
  ///
  /// The payload is already encoded by HA. The firmware validates hex/length
  /// shape, queues TX work for the component loop, updates display/telemetry,
  /// and returns whether the request was accepted for execution. It does not
  /// derive protocol fields from fireplace state.
  bool tx(const std::string& request_id, const std::string& air_payload_hex, uint32_t payload_bit_length,
          uint8_t repeat_count, const std::string& status_text, int intended_power = -1, int intended_flame = -1,
          int intended_fan = -1, int intended_light = -1, int intended_pilot = -1, int intended_thermostat = -1,
          int intended_front = -1, int intended_aux = -1, const std::string& intended_action_label = "",
          const std::string& fireplace_name = "");
  /// Diagnostic service/action entrypoint for direct CC1101 test-pattern TX.
  ///
  /// This is an RF troubleshooting path and is not a Proflame2 semantic command.
  bool cc1101_test_pattern(const std::string& request_id, TestPatternMode mode, uint32_t duration_ms,
                           uint32_t period_us, const std::string& status_text);
  /// HA display synchronization entrypoint for intended/observed state hints.
  ///
  /// Display values are informational. They must not feed back into protocol
  /// encoding or active-listener acceptance decisions.
  void display_state_update(int intended_power = -1, int intended_flame = -1, int intended_fan = -1,
                            int intended_light = -1, int intended_pilot = -1, int intended_thermostat = -1,
                            int intended_front = -1, int intended_aux = -1,
                            const std::string& intended_action_label = "", const std::string& fireplace_name = "");

  const std::string& get_status_text() const {
    return this->status_text_;
  }
  const std::string& get_last_error() const {
    return this->last_error_;
  }
  const std::string& get_last_tx_result() const {
    return this->last_tx_result_;
  }
  const std::string& get_last_request_id() const {
    return this->last_request_id_;
  }
  const std::string& get_last_tx_path() const {
    return this->last_tx_path_;
  }
  const std::string& get_last_payload_hex() const {
    return this->last_payload_hex_;
  }
  const std::string& get_last_marcstate_before_tx() const {
    return this->last_marcstate_before_tx_;
  }
  const std::string& get_last_marcstate_after_tx() const {
    return this->last_marcstate_after_tx_;
  }
  const std::string& get_cc1101_partnum() const {
    return this->cc1101_partnum_;
  }
  const std::string& get_cc1101_version() const {
    return this->cc1101_version_;
  }
  uint32_t get_tx_success_count() const {
    return this->tx_success_count_;
  }
  uint32_t get_tx_failure_count() const {
    return this->tx_failure_count_;
  }
  uint32_t get_last_payload_length() const {
    return this->last_payload_length_;
  }
  uint32_t get_last_payload_bit_length() const {
    return this->last_payload_bit_length_;
  }
  uint8_t get_tx_repeat_count() const {
    return this->tx_repeat_count_;
  }
  uint8_t get_last_request_repeat_count() const {
    return this->last_request_repeat_count_;
  }
  uint32_t get_last_tx_elapsed_ms() const {
    return this->last_tx_elapsed_ms_;
  }
  uint8_t get_firmware_protocol_version() const {
    return 1;
  }
  uint8_t get_config_revision() const {
    return 1;
  }
  uint32_t get_rx_dropped_packet_count() const {
    return this->rx_dropped_packet_count_;
  }
  uint32_t get_rx_no_rf_capture_count() const {
    return this->rx_no_rf_capture_count_;
  }
  uint32_t get_rx_incomplete_fifo_count() const {
    return this->rx_incomplete_fifo_count_;
  }
  uint32_t get_rx_decode_failed_count() const {
    return this->rx_decode_failed_count_;
  }
  uint32_t get_rx_profile_mismatch_count() const {
    return this->rx_profile_mismatch_count_;
  }
  uint32_t get_rx_accepted_packet_count() const {
    return this->rx_accepted_packet_count_;
  }
  uint32_t get_rx_tx_suppressed_count() const {
    return this->rx_tx_suppressed_count_;
  }
  uint32_t get_rx_transport_unavailable_count() const {
    return this->rx_transport_unavailable_count_;
  }
  bool is_display_update_allowed() const;
  bool display_refresh_pending() const {
    return this->display_.display_refresh_pending;
  }
  void mark_display_refresh_applied() {
    this->display_.display_refresh_pending = false;
  }
  bool display_backlight_refresh_pending() const {
    return this->display_backlight_refresh_pending_;
  }
  void mark_display_backlight_refresh_applied() {
    this->display_backlight_refresh_pending_ = false;
    this->display_backlight_current_level_ = this->get_display_backlight_level();
  }
  float get_display_backlight_level() const;
  uint8_t get_display_dim_level() const {
    return this->display_dim_level_;
  }
  uint32_t get_display_dim_timeout_min() const {
    return this->display_dim_timeout_ms_ / 60000U;
  }
  bool get_display_wake_on_activity() const {
    return this->display_wake_on_activity_;
  }
  void set_display_fireplace_name(const std::string& value) {
    if (value.empty() || this->display_.fireplace_name == value) {
      return;
    }
    this->display_.fireplace_name = value;
    this->mark_display_dirty_();
  }
  void set_wifi_connected(bool value);
  void set_api_connected(bool value);
  void handle_api_client_connected(const std::string& client_info);
  void handle_api_client_disconnected(const std::string& client_info);
  void set_wifi_rssi_dbm(float value);
  void clear_wifi_rssi();
  void set_battery_percent(float value);
  void set_battery_voltage(float value);
  void set_battery_charging(bool value);
  void set_battery_usb_present(bool value);
  void clear_battery();
  bool get_display_debug_mode() const {
    return this->display_.display_debug_mode;
  }
  bool tx_debug_logging_enabled_() const;
  DisplayBodyMode get_display_body_mode() const;
  const char* get_display_body_mode_text() const;
  const std::string& get_display_fireplace_state_label() const {
    return this->display_.fireplace_state_label;
  }
  std::string get_display_header_name_text() const;
  std::string get_display_battery_text() const;
  std::string get_display_wifi_text() const;
  std::string get_display_api_text() const;
  std::string get_display_connection_text() const;
  std::string get_display_mode_badge_text() const;
  std::string get_display_left_details_text() const;
  const char* get_display_right_panel_title_text() const;
  std::string get_display_right_panel_row1_label_text() const;
  std::string get_display_right_panel_row1_value_text() const;
  std::string get_display_right_panel_row2_label_text() const;
  std::string get_display_right_panel_row2_value_text() const;
  std::string get_display_right_panel_row3_label_text() const;
  std::string get_display_right_panel_row3_value_text() const;
  std::string get_display_field_text_(int value) const;
  std::string get_display_power_value_text() const;
  std::string get_display_flame_value_text() const;
  std::string get_display_fan_value_text() const;
  std::string get_display_light_value_text() const;
  std::string get_display_pilot_value_text() const;
  std::string get_display_therm_value_text() const;
  std::string get_display_front_value_text() const;
  std::string get_display_aux_value_text() const;
  const std::string& get_display_last_action_text() const {
    return this->display_.last_action_text;
  }
  std::string get_display_last_action_age_text() const;
  std::string get_display_right_panel_text() const;
  const std::string& get_display_active_operation_title() const {
    return this->display_.active_operation_title;
  }
  const std::string& get_display_active_operation_detail() const {
    return this->display_.active_operation_detail;
  }
  const std::string& get_display_learn_step_title() const {
    return this->display_.learn_step_title;
  }
  const std::string& get_display_learn_instruction() const {
    return this->display_.learn_instruction;
  }
  const std::string& get_display_learn_status() const {
    return this->display_.learn_status;
  }
  void handle_center_button_press();
  /// Update guided-learning UI state from HA/config-flow orchestration.
  void set_learn_mode(bool active, const std::string& step_title, const std::string& instruction,
                      const std::string& status);
  void set_display_state_hint(const std::string& state_label);
  void set_display_fireplace_values(int flame, int fan, int light, int pilot, int front, int aux, bool thermostat,
                                    bool thermostat_known);
  /// Set manual diagnostic capture mode. Production RX uses active listener config.
  void set_capture_mode(const std::string& value);
  /// Configure strict FIFO active listening for one learned Proflame2 profile.
  ///
  /// When enabled, FIFO capture is started and only decoded packets matching
  /// `serial_id`, `c1/d1`, and `c2/d2` are published to HA. Disabling stops the
  /// FIFO capture path. Calling this repeatedly with unchanged values is a
  /// no-op except for preserving the requested active-listener state.
  void configure_active_listener(bool enabled, uint32_t serial_id, uint8_t c1, uint8_t d1, uint8_t c2, uint8_t d2);
  /// Enable or disable bounded FIFO rolling capture.
  ///
  /// This owns byte-window acquisition only. Semantic publication is handled by
  /// active-listener decode/filter policy after FIFO windows are scanned.
  void set_fifo_capture_enabled(bool value);
#if PROFLAME2_TEMBED_DEBUG
  // Deep FIFO diagnostics are compiled only for debug firmware builds.
  void set_rx_fifo_profile(const std::string& value);
  void complete_rx_fifo_capture();
  void run_rx_fifo_probe();
#endif

  struct RadioRuntime;

protected:
  // TX/runtime coordination. `TxController` validates transport-level request
  // shape; this shell queues work, owns the radio runtime, and protects the
  // invariant that TX pauses/restores any active FIFO RX state.
  bool initialize_radio_(std::string* error);
  bool is_busy_() const;
  bool is_radio_runtime_available_() const;
  bool ensure_radio_runtime_(RadioRuntimeStartReason reason);
  bool enqueue_tx_(const std::string& request_id, const std::string& air_payload_hex, std::vector<uint8_t>&& payload,
                   uint32_t payload_bit_length, uint8_t repeat_count, const std::string& status_text);
  bool enqueue_test_pattern_(const std::string& request_id, uint32_t duration_ms, uint32_t period_us,
                             TestPatternMode mode, const std::string& status_text);
  void log_rf_path_for_tx_(uint32_t payload_length, uint32_t effective_payload_bit_length, uint8_t repeat_count) const;
  void log_rf_path_for_test_pattern_(TestPatternMode mode, uint32_t duration_ms, uint32_t period_us) const;
  void process_pending_operation_();
  void consume_radio_runtime_events_();
  void drain_deferred_debug_trace_();
  void store_deferred_debug_trace_(const TXTimingDiagnostics& timing, TXMode tx_mode);
  void clear_deferred_debug_trace_();

  // Telemetry/display publication. These helpers cache HA sensor/text updates
  // and keep display state informational rather than protocol-authoritative.
  void publish_telemetry_();
  void mark_display_dirty_();
  void mark_display_activity_(bool wake_display, bool center_button = false);
  void set_display_dimmed_(bool dimmed);
  DisplayRightPanelPage get_effective_right_panel_page_() const;
  void cycle_right_panel_page_();
  void update_display_runtime_state_(const std::string& title, const std::string& detail, uint32_t expiry_ms);
  void apply_pending_display_intent_(const std::string& request_id);
  void clear_pending_display_intent_();
  void update_display_from_telemetry_();
  void refresh_status_text_();
  void update_rx_runtime_display_state_();

  // FIFO RX coordination. `FifoRxController` stores bounded byte windows; this
  // shell configures CC1101, drains FIFO bytes, and hands windows to
  // active-listener policy.
  bool configure_rx_fifo_capture_mode_(std::string& error);
  void reset_rx_fifo_rolling_capture_(uint32_t enable_tick_ms);
  void poll_rx_fifo_capture_();
  void record_rx_fifo_byte_(uint8_t value, uint32_t tick_ms);
  void maybe_auto_complete_rx_fifo_capture_();
  void finalize_rx_fifo_capture_(const char* reason);
  bool dump_rx_fifo_rolling_capture_(const char* reason);

  // Active-listener semantic boundary. `ActiveListenerController` decides
  // accept/reject/duplicate; this shell updates counters and publishes accepted
  // learned-profile matches to HA.
  void publish_decoded_rx_packet_(uint32_t export_id, const Proflame2DecodedPacket& decoded,
                                  const uint8_t* selected_bytes, uint16_t selected_count, uint32_t complete_ms,
                                  uint32_t post_last_byte_quiet_ms, const char* reason);
  void record_rx_dropped_packet_(const char* stage, const char* reason, const Proflame2DecodedPacket* decoded,
                                 const uint8_t* selected_bytes, uint16_t selected_count, uint32_t complete_ms,
                                 uint32_t post_last_byte_quiet_ms);
  void record_rx_idle_noise_(const char* reason);
  void record_rx_accepted_packet_(const Proflame2DecodedPacket& decoded, uint32_t complete_ms, uint16_t selected_count);
  void increment_rx_suppressed_for_tx_();
  void increment_rx_transport_unavailable_();
  void restore_rx_after_tx_if_needed_();
  void maybe_log_rx_fifo_capture_status_();
  const char* rx_fifo_profile_name_() const;

  // Battery/board diagnostics are intentionally isolated from RF timing paths.
  void poll_battery_status_();
  uint8_t wifi_bars_from_rssi_(float dbm) const;

  uint32_t tx_frequency_hz_{314973000};
  uint32_t rx_frequency_hz_{314973000};
  uint32_t data_rate_bps_{2400};
  uint8_t tx_repeat_count_{5};
  uint32_t inter_frame_gap_us_{4450};
  uint32_t post_frame_idle_gap_us_{0};
  TXMode tx_mode_{TXMode::CONTINUOUS_BURST};
  std::string tx_mode_requested_{"continuous_burst"};
  NativeGroupTimingProfile native_group_timing_profile_{NativeGroupTimingProfile::YARDSTICK_COMPAT};
  std::string native_group_timing_profile_requested_{"yardstick_compat"};
  NativeGroupRepeatBoundaryMode native_group_repeat_boundary_mode_{NativeGroupRepeatBoundaryMode::CONTINUOUS_TX};
  std::string native_group_repeat_boundary_mode_requested_{"continuous_tx"};
  uint32_t pre_burst_low_us_{0};
  uint32_t pre_frame_low_us_{0};
  uint8_t diagnostic_repeat_count_override_{0};
  uint32_t payload_bit_length_override_{0};
  AsyncTxDataPin async_tx_data_pin_{AsyncTxDataPin::GDO0};

  GPIOPin* board_power_enable_pin_{nullptr};
  GPIOPin* cc1101_gdo0_pin_{nullptr};
  GPIOPin* cc1101_gdo2_pin_{nullptr};
  BatteryMonitor battery_monitor_{};
  GPIOPin* rf_switch_sw1_pin_{nullptr};
  GPIOPin* rf_switch_sw0_pin_{nullptr};
  text_sensor::TextSensor* endpoint_status_sensor_{nullptr};
  text_sensor::TextSensor* last_error_sensor_{nullptr};
  text_sensor::TextSensor* last_tx_result_sensor_{nullptr};
  text_sensor::TextSensor* last_request_id_sensor_{nullptr};
  text_sensor::TextSensor* last_tx_path_sensor_{nullptr};
  text_sensor::TextSensor* last_payload_hex_sensor_{nullptr};
  text_sensor::TextSensor* last_marcstate_before_tx_sensor_{nullptr};
  text_sensor::TextSensor* last_marcstate_after_tx_sensor_{nullptr};
  text_sensor::TextSensor* cc1101_partnum_sensor_{nullptr};
  text_sensor::TextSensor* cc1101_version_sensor_{nullptr};
  text_sensor::TextSensor* rx_last_rejection_snapshot_sensor_{nullptr};
  sensor::Sensor* tx_success_count_sensor_{nullptr};
  sensor::Sensor* tx_failure_count_sensor_{nullptr};
  sensor::Sensor* last_payload_length_sensor_{nullptr};
  sensor::Sensor* last_request_repeat_count_sensor_{nullptr};
  sensor::Sensor* last_tx_elapsed_ms_sensor_{nullptr};
  sensor::Sensor* tx_repeat_count_sensor_{nullptr};
  sensor::Sensor* firmware_protocol_version_sensor_{nullptr};
  sensor::Sensor* config_revision_sensor_{nullptr};
  sensor::Sensor* battery_percent_sensor_{nullptr};
  sensor::Sensor* rx_dropped_packet_count_sensor_{nullptr};
  sensor::Sensor* rx_no_rf_capture_count_sensor_{nullptr};
  sensor::Sensor* rx_incomplete_fifo_count_sensor_{nullptr};
  sensor::Sensor* rx_decode_failed_count_sensor_{nullptr};
  sensor::Sensor* rx_profile_mismatch_count_sensor_{nullptr};
  sensor::Sensor* rx_accepted_packet_count_sensor_{nullptr};
  sensor::Sensor* rx_tx_suppressed_count_sensor_{nullptr};
  sensor::Sensor* rx_transport_unavailable_count_sensor_{nullptr};

  std::string status_text_{"booting"};
  std::string last_error_{};
  std::string last_tx_result_{"none"};
  std::string last_request_id_{};
  std::string last_tx_path_{"none"};
  std::string last_payload_hex_{};
  std::string last_marcstate_before_tx_{"unknown"};
  std::string last_marcstate_after_tx_{"unknown"};
  std::string cc1101_partnum_{"unknown"};
  std::string cc1101_version_{"unknown"};
  uint32_t tx_success_count_{0};
  uint32_t tx_failure_count_{0};
  uint32_t last_payload_length_{0};
  uint32_t last_payload_bit_length_{0};
  uint32_t last_tx_elapsed_ms_{0};
  uint8_t last_request_repeat_count_{0};
  RadioRuntime* radio_runtime_{nullptr};
  RadioRuntimeState radio_runtime_state_{RadioRuntimeState::IDLE};
  RadioRuntimeInitState radio_runtime_init_state_{RadioRuntimeInitState::UNINITIALIZED};
  bool radio_runtime_creation_failed_{false};
  uint32_t radio_runtime_create_failures_{0};
  bool tx_in_progress_{false};
  bool deferred_debug_trace_valid_{false};
  TXTimingDiagnostics deferred_debug_timing_{};
  TXMode deferred_debug_tx_mode_{TXMode::REPEATED_STROBE};
  uint8_t deferred_debug_phase_{0};
  uint8_t deferred_debug_repeat_index_{0};
  uint8_t deferred_debug_bit_index_{0};
  std::string published_status_text_cache_;
  std::string published_last_error_cache_;
  std::string published_last_tx_result_cache_;
  std::string published_last_request_id_cache_;
  std::string published_last_tx_path_cache_;
  std::string published_last_payload_hex_cache_;
  std::string published_last_marcstate_before_tx_cache_;
  std::string published_last_marcstate_after_tx_cache_;
  std::string published_cc1101_partnum_cache_;
  std::string published_cc1101_version_cache_;
  uint32_t published_tx_success_count_cache_{UINT32_MAX};
  uint32_t published_tx_failure_count_cache_{UINT32_MAX};
  uint32_t published_last_payload_length_cache_{UINT32_MAX};
  uint32_t published_last_tx_elapsed_ms_cache_{UINT32_MAX};
  uint8_t published_last_request_repeat_count_cache_{UINT8_MAX};
  uint8_t published_tx_repeat_count_cache_{UINT8_MAX};
  uint8_t published_firmware_protocol_version_cache_{UINT8_MAX};
  uint8_t published_config_revision_cache_{UINT8_MAX};
  uint32_t published_rx_dropped_packet_count_cache_{UINT32_MAX};
  uint32_t published_rx_no_rf_capture_count_cache_{UINT32_MAX};
  uint32_t published_rx_incomplete_fifo_count_cache_{UINT32_MAX};
  uint32_t published_rx_decode_failed_count_cache_{UINT32_MAX};
  uint32_t published_rx_profile_mismatch_count_cache_{UINT32_MAX};
  uint32_t published_rx_accepted_packet_count_cache_{UINT32_MAX};
  uint32_t published_rx_tx_suppressed_count_cache_{UINT32_MAX};
  uint32_t published_rx_transport_unavailable_count_cache_{UINT32_MAX};
  std::string published_rx_last_rejection_snapshot_cache_;
  uint32_t last_battery_poll_ms_{0};
  bool battery_percent_sensor_valid_cache_{false};
  float battery_percent_sensor_cache_{NAN};
  uint32_t display_dim_timeout_ms_{60000U};
  bool display_wake_on_activity_{true};
  uint8_t display_dim_level_{3};
  uint8_t ha_api_client_count_{0};
  uint32_t last_display_activity_ms_{0};
  bool display_backlight_refresh_pending_{true};
  float display_backlight_current_level_{1.0f};
  bool display_dim_deferred_{false};
  uint32_t rx_fifo_probe_sequence_{0};
  bool rx_fifo_capture_enabled_{false};
  bool rx_fifo_capture_export_busy_{false};
  bool rx_fifo_capture_configured_{false};
  bool rx_fifo_capture_finalize_in_progress_{false};
  bool rx_fifo_paused_for_tx_{false};
  bool rx_active_listener_requested_{false};
  bool rx_active_listener_filter_configured_{false};
  Proflame2DecodeProfile rx_active_listener_profile_{};
  ActiveListenerController active_listener_{};
#if PROFLAME2_TEMBED_DEBUG
  std::string rx_fifo_profile_{"rfcat_fixed_none_rfcat_wide"};
#endif
  uint32_t rx_fifo_capture_session_index_{0};
  FifoRxController rx_fifo_{};
  uint32_t rx_dropped_packet_count_{0};
  uint32_t rx_no_rf_capture_count_{0};
  uint32_t rx_incomplete_fifo_count_{0};
  uint32_t rx_decode_failed_count_{0};
  uint32_t rx_profile_mismatch_count_{0};
  uint32_t rx_accepted_packet_count_{0};
  uint32_t rx_tx_suppressed_count_{0};
  uint32_t rx_transport_unavailable_count_{0};
  uint32_t rx_idle_noise_count_{0};
  std::string rx_last_rejection_snapshot_;
  const char* rx_fifo_capture_last_stop_reason_{"none"};
  struct PendingDisplayIntent {
    bool valid{false};
    std::string request_id;
    int power{-1};
    int flame{-1};
    int fan{-1};
    int light{-1};
    int pilot{-1};
    int thermostat{-1};
    int front{-1};
    int aux{-1};
    std::string action_label;
  };

  PendingDisplayIntent pending_display_intent_{};
  DisplayViewModel display_{};
};

template <typename... Ts> class Proflame2TEmbedTxAction : public Action<Ts...> {
public:
  explicit Proflame2TEmbedTxAction(Proflame2TEmbedComponent* parent) : parent_(parent) {}

  TEMPLATABLE_VALUE(std::string, request_id)
  TEMPLATABLE_VALUE(std::string, air_payload_hex)
  TEMPLATABLE_VALUE(uint32_t, payload_bit_length)
  TEMPLATABLE_VALUE(uint8_t, repeat_count)
  TEMPLATABLE_VALUE(std::string, status_text)

  void play(Ts... x) {
    this->play_impl_(x...);
  }

  void play(const Ts&... x) {
    this->play_impl_(x...);
  }

protected:
  void play_impl_(const Ts&... x) {
    this->parent_->tx(this->request_id_.value(x...), this->air_payload_hex_.value(x...),
                      this->payload_bit_length_.value(x...), this->repeat_count_.value(x...),
                      this->status_text_.value(x...));
  }
  Proflame2TEmbedComponent* parent_;
};

template <typename... Ts> class Proflame2TEmbedTxStatefulAction : public Action<Ts...> {
public:
  explicit Proflame2TEmbedTxStatefulAction(Proflame2TEmbedComponent* parent) : parent_(parent) {}

  TEMPLATABLE_VALUE(std::string, request_id)
  TEMPLATABLE_VALUE(std::string, air_payload_hex)
  TEMPLATABLE_VALUE(uint32_t, payload_bit_length)
  TEMPLATABLE_VALUE(uint8_t, repeat_count)
  TEMPLATABLE_VALUE(std::string, status_text)
  TEMPLATABLE_VALUE(int32_t, intended_power)
  TEMPLATABLE_VALUE(int32_t, intended_flame)
  TEMPLATABLE_VALUE(int32_t, intended_fan)
  TEMPLATABLE_VALUE(int32_t, intended_light)
  TEMPLATABLE_VALUE(int32_t, intended_pilot)
  TEMPLATABLE_VALUE(int32_t, intended_thermostat)
  TEMPLATABLE_VALUE(int32_t, intended_front)
  TEMPLATABLE_VALUE(int32_t, intended_aux)
  TEMPLATABLE_VALUE(std::string, intended_action_label)
  TEMPLATABLE_VALUE(std::string, fireplace_name)

  void play(Ts... x) {
    this->play_impl_(x...);
  }

  void play(const Ts&... x) {
    this->play_impl_(x...);
  }

protected:
  void play_impl_(const Ts&... x) {
    this->parent_->tx(
        this->request_id_.value(x...), this->air_payload_hex_.value(x...), this->payload_bit_length_.value(x...),
        this->repeat_count_.value(x...), this->status_text_.value(x...), this->intended_power_.value(x...),
        this->intended_flame_.value(x...), this->intended_fan_.value(x...), this->intended_light_.value(x...),
        this->intended_pilot_.value(x...), this->intended_thermostat_.value(x...), this->intended_front_.value(x...),
        this->intended_aux_.value(x...), this->intended_action_label_.value(x...), this->fireplace_name_.value(x...));
  }
  Proflame2TEmbedComponent* parent_;
};

template <typename... Ts> class Proflame2TEmbedDisplayStateUpdateAction : public Action<Ts...> {
public:
  explicit Proflame2TEmbedDisplayStateUpdateAction(Proflame2TEmbedComponent* parent) : parent_(parent) {}

  TEMPLATABLE_VALUE(int32_t, intended_power)
  TEMPLATABLE_VALUE(int32_t, intended_flame)
  TEMPLATABLE_VALUE(int32_t, intended_fan)
  TEMPLATABLE_VALUE(int32_t, intended_light)
  TEMPLATABLE_VALUE(int32_t, intended_pilot)
  TEMPLATABLE_VALUE(int32_t, intended_thermostat)
  TEMPLATABLE_VALUE(int32_t, intended_front)
  TEMPLATABLE_VALUE(int32_t, intended_aux)
  TEMPLATABLE_VALUE(std::string, intended_action_label)
  TEMPLATABLE_VALUE(std::string, fireplace_name)

  void play(Ts... x) {
    this->play_impl_(x...);
  }
  void play(const Ts&... x) {
    this->play_impl_(x...);
  }

protected:
  void play_impl_(const Ts&... x) {
    this->parent_->display_state_update(this->intended_power_.value(x...), this->intended_flame_.value(x...),
                                        this->intended_fan_.value(x...), this->intended_light_.value(x...),
                                        this->intended_pilot_.value(x...), this->intended_thermostat_.value(x...),
                                        this->intended_front_.value(x...), this->intended_aux_.value(x...),
                                        this->intended_action_label_.value(x...), this->fireplace_name_.value(x...));
  }
  Proflame2TEmbedComponent* parent_;
};

template <typename... Ts> class Proflame2TEmbedTestPatternAction : public Action<Ts...> {
public:
  explicit Proflame2TEmbedTestPatternAction(Proflame2TEmbedComponent* parent) : parent_(parent) {}

  TEMPLATABLE_VALUE(std::string, request_id)
  TEMPLATABLE_VALUE(TestPatternMode, mode)
  TEMPLATABLE_VALUE(uint32_t, duration_ms)
  TEMPLATABLE_VALUE(uint32_t, period_us)
  TEMPLATABLE_VALUE(std::string, status_text)

  void play(Ts... x) {
    this->play_impl_(x...);
  }

  void play(const Ts&... x) {
    this->play_impl_(x...);
  }

protected:
  void play_impl_(const Ts&... x) {
    this->parent_->cc1101_test_pattern(this->request_id_.value(x...), this->mode_.value(x...),
                                       this->duration_ms_.value(x...), this->period_us_.value(x...),
                                       this->status_text_.value(x...));
  }
  Proflame2TEmbedComponent* parent_;
};

} // namespace proflame2_tembed
} // namespace esphome
