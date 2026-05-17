// Battery/PMIC polling support for the LilyGO T-Embed endpoint.
//
// This module owns only I2C battery acquisition and failure bookkeeping. The
// main component remains responsible for display state and Home Assistant
// sensor publication.

#pragma once

#include <cstdint>

#include "esphome/components/i2c/i2c.h"

namespace esphome {
namespace proflame2_tembed {

struct BatterySnapshot {
  bool gauge_valid{false};
  bool charger_valid{false};
  float percent{0.0f};
  float voltage{0.0f};
  bool usb_present{false};
  bool charging{false};
};

class BatteryMonitor {
public:
  void setup(i2c::I2CBus* bus);

  BatterySnapshot poll(i2c::I2CDevice* gauge_device);

  uint8_t read_failures() const {
    return this->read_failures_;
  }

private:
  bool read_word_(i2c::I2CDevice* device, uint8_t reg, uint16_t* value);
  bool read_byte_(i2c::I2CDevice* device, uint8_t reg, uint8_t* value);

  i2c::I2CDevice charger_i2c_{};
  uint8_t read_failures_{0};
  bool log_ok_{false};
  bool log_failure_reported_{false};
};

} // namespace proflame2_tembed
} // namespace esphome
