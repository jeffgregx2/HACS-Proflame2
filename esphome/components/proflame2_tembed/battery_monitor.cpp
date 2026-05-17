#include "battery_monitor.h"

#include "esphome/core/log.h"

namespace esphome {
namespace proflame2_tembed {

static const char* const TAG = "proflame2_battery";

static constexpr uint8_t BQ27220_REG_VOLTAGE = 0x08;
static constexpr uint8_t BQ27220_REG_SOC = 0x2C;
static constexpr uint8_t BQ25896_REG_STATUS = 0x0B;
static constexpr uint8_t BQ25896_I2C_ADDRESS = 0x6B;

void BatteryMonitor::setup(i2c::I2CBus* bus) {
  this->charger_i2c_.set_i2c_bus(bus);
  this->charger_i2c_.set_i2c_address(BQ25896_I2C_ADDRESS);
}

BatterySnapshot BatteryMonitor::poll(i2c::I2CDevice* gauge_device) {
  BatterySnapshot snapshot;
  uint16_t soc_raw = 0;
  uint16_t voltage_mv = 0;
  const bool soc_ok = this->read_word_(gauge_device, BQ27220_REG_SOC, &soc_raw);
  const bool voltage_ok = this->read_word_(gauge_device, BQ27220_REG_VOLTAGE, &voltage_mv);
  const bool gauge_ok = soc_ok && voltage_ok && soc_raw <= 100U && voltage_mv >= 2500U && voltage_mv <= 5000U;

  if (gauge_ok) {
    this->read_failures_ = 0;
    this->log_failure_reported_ = false;
    snapshot.gauge_valid = true;
    snapshot.percent = static_cast<float>(soc_raw);
    snapshot.voltage = static_cast<float>(voltage_mv) / 1000.0f;

    uint8_t charger_status = 0;
    if (this->read_byte_(&this->charger_i2c_, BQ25896_REG_STATUS, &charger_status)) {
      const uint8_t charge_state = static_cast<uint8_t>((charger_status >> 3U) & 0x03U);
      snapshot.charger_valid = true;
      snapshot.usb_present = (charger_status & 0x04U) != 0U;
      snapshot.charging = charge_state == 0x01U || charge_state == 0x02U;
    }
    if (!this->log_ok_) {
      ESP_LOGD(TAG, "Battery poll ok soc=%u%% voltage=%umV", static_cast<unsigned>(soc_raw),
               static_cast<unsigned>(voltage_mv));
      this->log_ok_ = true;
    }
    return snapshot;
  }

  if (this->read_failures_ < 0xFFU) {
    this->read_failures_++;
  }
  if (!this->log_failure_reported_) {
    ESP_LOGW(TAG, "Battery poll failed soc_ok=%s voltage_ok=%s", YESNO(soc_ok), YESNO(voltage_ok));
    this->log_failure_reported_ = true;
  }
  this->log_ok_ = false;
  return snapshot;
}

bool BatteryMonitor::read_word_(i2c::I2CDevice* device, uint8_t reg, uint16_t* value) {
  if (device == nullptr || value == nullptr) {
    return false;
  }
  uint8_t raw[2] = {0, 0};
  if (!device->read_bytes(reg, raw, 2)) {
    return false;
  }
  *value = static_cast<uint16_t>(raw[0]) | (static_cast<uint16_t>(raw[1]) << 8U);
  return true;
}

bool BatteryMonitor::read_byte_(i2c::I2CDevice* device, uint8_t reg, uint8_t* value) {
  if (device == nullptr || value == nullptr) {
    return false;
  }
  return device->read_bytes(reg, value, 1);
}

} // namespace proflame2_tembed
} // namespace esphome
