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

} // namespace proflame2_tembed
} // namespace esphome
