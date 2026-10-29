#include "rf_band.h"

namespace esphome {
namespace proflame2_tembed {

bool resolve_rf_band_configuration(RFBand band, RFBandConfiguration& configuration) {
  switch (band) {
  case RFBand::BAND_315:
    configuration = {314973000U, true, false, "315 MHz"};
    return true;
  case RFBand::BAND_433:
    configuration = {433920000U, true, true, "433.92 MHz"};
    return true;
  }

  return false;
}

bool resolve_tx_frequency(RFBand band, int32_t offset_hz, uint32_t& frequency_hz) {
  RFBandConfiguration configuration{};
  if (!resolve_rf_band_configuration(band, configuration) || offset_hz < -MAX_TX_FREQUENCY_OFFSET_HZ ||
      offset_hz > MAX_TX_FREQUENCY_OFFSET_HZ) {
    return false;
  }
  frequency_hz = static_cast<uint32_t>(static_cast<int64_t>(configuration.frequency_hz) + offset_hz);
  return true;
}

} // namespace proflame2_tembed
} // namespace esphome
