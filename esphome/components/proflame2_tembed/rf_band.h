// RF-band selection for the LilyGO T-Embed CC1101 board.
//
// The board's RF switch must be selected together with the CC1101 carrier
// frequency. Keep this mapping independent of ESPHome so it can be host tested.

#pragma once

#include <cstdint>

namespace esphome {
namespace proflame2_tembed {

enum class RFBand : uint8_t {
  BAND_315 = 0,
  BAND_433 = 1,
};

struct RFBandConfiguration {
  uint32_t frequency_hz;
  bool rf_switch_sw1_high;
  bool rf_switch_sw0_high;
  const char* name;
};

/// Resolve one supported RF band. Returns false for an invalid enum value.
bool resolve_rf_band_configuration(RFBand band, RFBandConfiguration& configuration);

} // namespace proflame2_tembed
} // namespace esphome
