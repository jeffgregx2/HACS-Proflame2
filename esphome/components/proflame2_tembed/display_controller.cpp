#include "display_controller.h"

#include <cmath>

#include "esphome/core/helpers.h"

namespace esphome {
namespace proflame2_tembed {

DisplayBodyMode DisplayController::body_mode(const DisplayViewModel& view) {
  if (view.learn_active) {
    return DisplayBodyMode::LEARN;
  }
  if (view.active_operation) {
    return DisplayBodyMode::ACTIVE;
  }
  if (view.display_debug_mode) {
    return DisplayBodyMode::DEBUG;
  }
  return DisplayBodyMode::PRODUCTION;
}

const char* DisplayController::body_mode_text(DisplayBodyMode mode) {
  switch (mode) {
  case DisplayBodyMode::LEARN:
    return "LEARN";
  case DisplayBodyMode::ACTIVE:
    return "ACTIVE";
  case DisplayBodyMode::DEBUG:
    return "DEBUG";
  case DisplayBodyMode::PRODUCTION:
  default:
    return "PROD";
  }
}

std::string DisplayController::header_name_text(const DisplayViewModel& view) {
  return view.fireplace_name;
}

std::string DisplayController::battery_text(const DisplayViewModel& view) {
  if (!view.battery_valid) {
    return "--%";
  }
  return str_sprintf("%u%%", static_cast<unsigned>(std::lround(view.battery_percent)));
}

std::string DisplayController::wifi_text(const DisplayViewModel& view) {
  if (!view.wifi_connected || !view.wifi_rssi_valid || view.wifi_bars == 0U) {
    return "--";
  }
  return str_sprintf("%u", static_cast<unsigned>(view.wifi_bars));
}

std::string DisplayController::api_text(const DisplayViewModel& view) {
  return view.api_connected ? "OK" : "--";
}

std::string DisplayController::connection_text(const DisplayViewModel& view) {
  return str_sprintf("W:%s A:%s", wifi_text(view).c_str(), api_text(view).c_str());
}

std::string DisplayController::field_text(int value) {
  return value >= 0 ? str_sprintf("%d", value) : std::string("--");
}

std::string DisplayController::power_value_text(const DisplayViewModel& view) {
  return view.fireplace_power_known ? (view.fireplace_power ? "On" : "Off") : "--";
}

std::string DisplayController::flame_value_text(const DisplayViewModel& view) {
  return field_text(view.fireplace_flame);
}

std::string DisplayController::fan_value_text(const DisplayViewModel& view) {
  return field_text(view.fireplace_fan);
}

std::string DisplayController::light_value_text(const DisplayViewModel& view) {
  return field_text(view.fireplace_light);
}

std::string DisplayController::pilot_value_text(const DisplayViewModel& view) {
  return field_text(view.fireplace_pilot);
}

std::string DisplayController::thermostat_value_text(const DisplayViewModel& view) {
  return view.fireplace_thermostat_known ? (view.fireplace_thermostat ? "On" : "Off") : "--";
}

std::string DisplayController::front_value_text(const DisplayViewModel& view) {
  return field_text(view.fireplace_front);
}

std::string DisplayController::aux_value_text(const DisplayViewModel& view) {
  return field_text(view.fireplace_aux);
}

std::string DisplayController::left_details_text(const DisplayViewModel& view) {
  return str_sprintf("Power: %s\nFlame: %s\nFan: %s\nLight: %s\nPilot: %s\nTherm: %s\nFront: %s\nAux: %s",
                     power_value_text(view).c_str(), flame_value_text(view).c_str(), fan_value_text(view).c_str(),
                     light_value_text(view).c_str(), pilot_value_text(view).c_str(),
                     thermostat_value_text(view).c_str(), front_value_text(view).c_str(), aux_value_text(view).c_str());
}

std::string DisplayController::age_text(uint32_t delta_ms) {
  if (delta_ms < 1000U) {
    return "just now";
  }
  const uint32_t seconds = delta_ms / 1000U;
  if (seconds < 60U) {
    return str_sprintf("%us ago", static_cast<unsigned>(seconds));
  }
  const uint32_t minutes = seconds / 60U;
  if (minutes < 60U) {
    return str_sprintf("%um ago", static_cast<unsigned>(minutes));
  }
  const uint32_t hours = minutes / 60U;
  return str_sprintf("%uh ago", static_cast<unsigned>(hours));
}

DisplayRightPanelPage DisplayController::effective_right_panel_page(const DisplayViewModel& view) {
  if (view.active_operation) {
    return DisplayRightPanelPage::ACTIVITY;
  }
  if (view.right_panel_page == DisplayRightPanelPage::DEBUG && !view.display_debug_mode) {
    return DisplayRightPanelPage::ACTIVITY;
  }
  return view.right_panel_page;
}

DisplayRightPanelPage DisplayController::next_right_panel_page(const DisplayViewModel& view) {
  switch (view.right_panel_page) {
  case DisplayRightPanelPage::ACTIVITY:
    return DisplayRightPanelPage::LISTEN;
  case DisplayRightPanelPage::LISTEN:
    return view.display_debug_mode ? DisplayRightPanelPage::DEBUG : DisplayRightPanelPage::ACTIVITY;
  case DisplayRightPanelPage::DEBUG:
  default:
    return DisplayRightPanelPage::ACTIVITY;
  }
}

} // namespace proflame2_tembed
} // namespace esphome
