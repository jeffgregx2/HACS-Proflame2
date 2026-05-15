// Stateless display view helpers for the T-Embed LVGL UI.
//
// These helpers derive labels/text from DisplayViewModel only. They do not own
// HA state, protocol state, RF state, or LVGL objects.

#pragma once

#include <cstdint>
#include <string>

#include "display_state.h"

namespace esphome {
namespace proflame2_tembed {

class DisplayController {
public:
  static DisplayBodyMode body_mode(const DisplayViewModel& view);
  static const char* body_mode_text(DisplayBodyMode mode);
  static std::string header_name_text(const DisplayViewModel& view);
  static std::string battery_text(const DisplayViewModel& view);
  static std::string wifi_text(const DisplayViewModel& view);
  static std::string api_text(const DisplayViewModel& view);
  static std::string connection_text(const DisplayViewModel& view);
  static std::string field_text(int value);
  static std::string power_value_text(const DisplayViewModel& view);
  static std::string flame_value_text(const DisplayViewModel& view);
  static std::string fan_value_text(const DisplayViewModel& view);
  static std::string light_value_text(const DisplayViewModel& view);
  static std::string pilot_value_text(const DisplayViewModel& view);
  static std::string thermostat_value_text(const DisplayViewModel& view);
  static std::string front_value_text(const DisplayViewModel& view);
  static std::string aux_value_text(const DisplayViewModel& view);
  static std::string left_details_text(const DisplayViewModel& view);
  static std::string age_text(uint32_t delta_ms);
  static DisplayRightPanelPage effective_right_panel_page(const DisplayViewModel& view);
  static DisplayRightPanelPage next_right_panel_page(const DisplayViewModel& view);
};

} // namespace proflame2_tembed
} // namespace esphome
