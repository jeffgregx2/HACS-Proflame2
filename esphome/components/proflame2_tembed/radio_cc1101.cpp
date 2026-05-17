#include "radio_cc1101.h"

#include <inttypes.h>

#include "esphome/core/log.h"

namespace esphome {
namespace proflame2_tembed {

static const char* const TAG = "proflame2_cc1101";

static constexpr uint32_t CC1101_XOSC_HZ = 26000000;
static constexpr const char* const PROFLAME_BUILD_MARKER = "native-groups-revert-check-20260430";

static constexpr uint8_t CC1101_WRITE_BURST = 0x40;
static constexpr uint8_t CC1101_READ_SINGLE = 0x80;
static constexpr uint8_t CC1101_READ_BURST = 0xC0;
static constexpr uint8_t CC1101_READ_STATUS = 0xC0;

void RadioCC1101::log_runtime_register_snapshot(const char* stage) {
  const char* resolved_stage = stage != nullptr ? stage : "runtime";
  const uint8_t iocfg2 = this->read_register_(CC1101_IOCFG2);
  const uint8_t iocfg0 = this->read_register_(CC1101_IOCFG0);
  const uint8_t pktctrl1 = this->read_register_(CC1101_PKTCTRL1);
  const uint8_t pktctrl0 = this->read_register_(CC1101_PKTCTRL0);
  const uint8_t fsctrl1 = this->read_register_(CC1101_FSCTRL1);
  const uint8_t fsctrl0 = this->read_register_(CC1101_FSCTRL0);
  const uint8_t freq2 = this->read_register_(CC1101_FREQ2);
  const uint8_t freq1 = this->read_register_(CC1101_FREQ1);
  const uint8_t freq0 = this->read_register_(CC1101_FREQ0);
  const uint8_t mdmcfg4 = this->read_register_(CC1101_MDMCFG4);
  const uint8_t mdmcfg3 = this->read_register_(CC1101_MDMCFG3);
  const uint8_t mdmcfg2 = this->read_register_(CC1101_MDMCFG2);
  const uint8_t mdmcfg1 = this->read_register_(CC1101_MDMCFG1);
  const uint8_t mdmcfg0 = this->read_register_(CC1101_MDMCFG0);
  const uint8_t deviatn = this->read_register_(CC1101_DEVIATN);
  const uint8_t foccfg = this->read_register_(CC1101_FOCCFG);
  const uint8_t bscfg = this->read_register_(CC1101_BSCFG);
  const uint8_t agcctrl2 = this->read_register_(CC1101_AGCCTRL2);
  const uint8_t agcctrl1 = this->read_register_(CC1101_AGCCTRL1);
  const uint8_t agcctrl0 = this->read_register_(CC1101_AGCCTRL0);
  const uint8_t frend1 = this->read_register_(CC1101_FREND1);
  const uint8_t frend0 = this->read_register_(CC1101_FREND0);
  const uint8_t mcsm1 = this->read_register_(CC1101_MCSM1);
  const uint8_t mcsm0 = this->read_register_(CC1101_MCSM0);
  const uint8_t fscal3 = this->read_register_(CC1101_FSCAL3);
  const uint8_t fscal2 = this->read_register_(CC1101_FSCAL2);
  const uint8_t fscal1 = this->read_register_(CC1101_FSCAL1);
  const uint8_t fscal0 = this->read_register_(CC1101_FSCAL0);
  const uint8_t test2 = this->read_register_(CC1101_TEST2);
  const uint8_t test1 = this->read_register_(CC1101_TEST1);
  const uint8_t test0 = this->read_register_(CC1101_TEST0);
  const uint8_t pktstatus = this->read_status_register_(CC1101_PKTSTATUS);
  const uint8_t rssi = this->read_status_register_(CC1101_RSSI);
  const uint8_t lqi = this->read_status_register_(CC1101_LQI);
  const uint8_t rxbytes = this->read_status_register_(CC1101_RXBYTES);
  const uint8_t pa_table0 = this->read_register_(CC1101_PATABLE);
  std::array<uint8_t, 8> pa_table{};
  this->read_burst_register_(CC1101_PATABLE, pa_table.data(), pa_table.size());
  const uint8_t marcstate = this->read_marcstate();
  ESP_LOGI(TAG,
           "CC1101 runtime snapshot stage=%s IOCFG2=0x%02X IOCFG0=0x%02X PKTCTRL1=0x%02X PKTCTRL0=0x%02X"
           " MDMCFG4=0x%02X MDMCFG3=0x%02X MDMCFG2=0x%02X MDMCFG1=0x%02X MDMCFG0=0x%02X"
           " DEVIATN=0x%02X FOCCFG=0x%02X BSCFG=0x%02X AGCCTRL2=0x%02X AGCCTRL1=0x%02X AGCCTRL0=0x%02X"
           " FREND1=0x%02X FREND0=0x%02X MCSM1=0x%02X MCSM0=0x%02X"
           " FSCTRL1=0x%02X FSCTRL0=0x%02X FREQ2=0x%02X FREQ1=0x%02X FREQ0=0x%02X"
           " FSCAL3=0x%02X FSCAL2=0x%02X FSCAL1=0x%02X FSCAL0=0x%02X"
           " TEST2=0x%02X TEST1=0x%02X TEST0=0x%02X"
           " PKTSTATUS=0x%02X RSSI=0x%02X LQI=0x%02X RXBYTES=0x%02X"
           " PATABLE0=0x%02X MARCSTATE=0x%02X(%s)",
           resolved_stage, iocfg2, iocfg0, pktctrl1, pktctrl0, mdmcfg4, mdmcfg3, mdmcfg2, mdmcfg1, mdmcfg0, deviatn,
           foccfg, bscfg, agcctrl2, agcctrl1, agcctrl0, frend1, frend0, mcsm1, mcsm0, fsctrl1, fsctrl0, freq2, freq1,
           freq0, fscal3, fscal2, fscal1, fscal0, test2, test1, test0, pktstatus, rssi, lqi, rxbytes, pa_table0,
           marcstate, marcstate_to_string_(marcstate));
  ESP_LOGI(TAG,
           "CC1101 PATABLE stage=%s effective_pa_entry0=0x%02X pa_table=[0]=0x%02X [1]=0x%02X [2]=0x%02X [3]=0x%02X "
           "[4]=0x%02X [5]=0x%02X [6]=0x%02X [7]=0x%02X",
           resolved_stage, pa_table[0], pa_table[0], pa_table[1], pa_table[2], pa_table[3], pa_table[4], pa_table[5],
           pa_table[6], pa_table[7]);
}

void RadioCC1101::set_idle() {
  this->last_sidled_status_ = this->strobe_(CC1101_SIDLE);
  this->last_sftx_status_ = this->strobe_(CC1101_SFTX);
  if (this->async_tx_pin_() != nullptr) {
    this->async_tx_pin_()->digital_write(false);
  }
}

bool RadioCC1101::write_register_(uint8_t address, uint8_t value) {
  this->enable();
  this->write_byte(address);
  this->write_byte(value);
  this->disable();
  return true;
}

uint8_t RadioCC1101::read_register_(uint8_t address) {
  this->enable();
  this->write_byte(address | CC1101_READ_SINGLE);
  const uint8_t value = this->read_byte();
  this->disable();
  return value;
}

uint8_t RadioCC1101::read_status_register_(uint8_t address) {
  this->enable();
  this->write_byte(address | CC1101_READ_STATUS);
  const uint8_t value = this->read_byte();
  this->disable();
  return value;
}

void RadioCC1101::read_burst_register_(uint8_t address, uint8_t* data, size_t length) {
  this->enable();
  this->write_byte(address | CC1101_READ_BURST);
  for (size_t index = 0; index < length; index++) {
    data[index] = this->read_byte();
  }
  this->disable();
}

void RadioCC1101::write_burst_register_(uint8_t address, const uint8_t* data, size_t length) {
  this->enable();
  this->write_byte(address | CC1101_WRITE_BURST);
  this->write_array(data, length);
  this->disable();
}

uint8_t RadioCC1101::strobe_(uint8_t command) {
  this->enable();
  const uint8_t response = this->transfer_byte(command);
  this->disable();
  return response;
}

const char* RadioCC1101::marcstate_to_string_(uint8_t marcstate) {
  switch (marcstate & 0x1F) {
  case 0x01:
    return "IDLE";
  case 0x13:
    return "TX";
  case 0x0D:
    return "RX";
  case 0x11:
    return "FSTXON";
  case 0x16:
    return "TXFIFO_UNDERFLOW";
  default:
    return "UNKNOWN";
  }
}

uint32_t RadioCC1101::compute_frequency_word_(uint32_t frequency_hz) {
  const uint64_t scaled = (static_cast<uint64_t>(frequency_hz) << 16U) / CC1101_XOSC_HZ;
  return static_cast<uint32_t>(scaled & 0xFFFFFFU);
}

void RadioCC1101::compute_drate_registers_(uint32_t data_rate_bps, uint8_t& mdmcfg4, uint8_t& mdmcfg3) {
  uint32_t best_error = UINT32_MAX;
  uint8_t best_exponent = 0;
  uint8_t best_mantissa = 0;

  for (uint8_t exponent = 0; exponent <= 0x0F; exponent++) {
    for (uint16_t mantissa = 0; mantissa <= 0xFF; mantissa++) {
      const uint64_t numerator = static_cast<uint64_t>(256U + mantissa) * (1ULL << exponent) * CC1101_XOSC_HZ;
      const uint32_t candidate = static_cast<uint32_t>(numerator / (1ULL << 28U));
      const uint32_t error = candidate > data_rate_bps ? candidate - data_rate_bps : data_rate_bps - candidate;
      if (error < best_error) {
        best_error = error;
        best_exponent = exponent;
        best_mantissa = static_cast<uint8_t>(mantissa);
      }
    }
  }

  mdmcfg4 = static_cast<uint8_t>(0xF0 | best_exponent);
  mdmcfg3 = best_mantissa;
}

} // namespace proflame2_tembed
} // namespace esphome
