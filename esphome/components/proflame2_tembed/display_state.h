// Display state for the T-Embed LVGL UI.
//
// Display data is informational only. The device must not infer authoritative
// fireplace state or generate Proflame2 RF payloads from display fields.

#pragma once

#include <cstdint>
#include <string>

namespace esphome {
namespace proflame2_tembed {

enum class DisplayBodyMode : uint8_t {
  PRODUCTION = 0,
  DEBUG = 1,
  ACTIVE = 2,
  LEARN = 3,
};

enum class DisplayRightPanelPage : uint8_t {
  ACTIVITY = 0,
  LISTEN = 1,
  DEBUG = 2,
};

struct DisplayViewModel {
  bool display_debug_mode{false};
  bool display_refresh_pending{true};
  DisplayRightPanelPage right_panel_page{DisplayRightPanelPage::ACTIVITY};
  bool display_dimmed{false};
  bool wifi_connected{false};
  bool api_connected{false};
  float wifi_rssi_dbm{0.0f};
  bool wifi_rssi_valid{false};
  uint8_t wifi_bars{0};
  float battery_percent{0.0f};
  float battery_voltage{0.0f};
  bool battery_charging{false};
  bool battery_usb_present{false};
  bool battery_valid{false};
  std::string fireplace_name{"---"};
  bool fireplace_power_known{false};
  bool fireplace_power{false};
  int fireplace_flame{-1};
  int fireplace_fan{-1};
  int fireplace_light{-1};
  int fireplace_pilot{-1};
  int fireplace_front{-1};
  int fireplace_aux{-1};
  bool fireplace_thermostat{false};
  bool fireplace_thermostat_known{false};
  std::string fireplace_state_label{"PF2 READY"};
  std::string last_action_text{"None"};
  uint32_t last_action_millis{0};
  bool active_operation{false};
  std::string active_operation_title;
  std::string active_operation_detail;
  uint32_t active_operation_expires_millis{0};
  std::string last_result{"none"};
  std::string last_error;
  bool learn_active{false};
  std::string learn_step_title{"Learn"};
  std::string learn_instruction{"Not active"};
  std::string learn_status{"idle"};
  std::string tx_mode{"unknown"};
  std::string native_group_timing_profile{"unknown"};
  std::string native_group_repeat_boundary_mode{"unknown"};
  uint8_t repeat_count{0};
  uint32_t payload_bits{0};
  uint32_t pcm_row_bits{0};
  std::string row_prefix{"------"};
  std::string decode_result{"tx_only"};
  uint32_t last_tx_elapsed_ms{0};
  std::string marcstate_before{"unknown"};
  std::string marcstate_after{"unknown"};
};

} // namespace proflame2_tembed
} // namespace esphome
