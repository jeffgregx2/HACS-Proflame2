#include "radio_cc1101.h"

#include <algorithm>

#include "esp_rom_sys.h"
#include "esp_timer.h"

namespace esphome {
namespace proflame2_tembed {

bool RadioCC1101::rx_fifo_probe(uint32_t frequency_hz, uint32_t data_rate_bps, uint32_t duration_ms,
                                RXFifoProbeResult& result, std::string& error) {
  result = RXFifoProbeResult{};
  result.frequency_hz = frequency_hz;
  result.data_rate_bps = data_rate_bps;
  result.requested_duration_ms = duration_ms;
  result.started_tick_ms = static_cast<uint32_t>(esp_timer_get_time() / 1000ULL);

  if (!this->initialized_) {
    error = "radio_not_initialized";
    return false;
  }

  result.partnum = this->read_partnum();
  result.version = this->read_version();
  result.marcstate_before = this->read_marcstate();

  uint8_t mdmcfg4 = 0;
  uint8_t mdmcfg3 = 0;
  compute_drate_registers_(data_rate_bps, mdmcfg4, mdmcfg3);
  const uint32_t frequency_word = compute_frequency_word_(frequency_hz);

  this->strobe_(CC1101_SIDLE);
  this->strobe_(CC1101_SFRX);
  this->strobe_(CC1101_SFTX);

  this->write_register_(CC1101_IOCFG2, 0x2E);
  this->write_register_(CC1101_IOCFG1, 0x2E);
  this->write_register_(CC1101_IOCFG0, 0x2E);
  this->write_register_(CC1101_FIFOTHR, 0x47);
  this->write_register_(CC1101_SYNC1, 0x00);
  this->write_register_(CC1101_SYNC0, 0x00);
  this->write_register_(CC1101_PKTLEN, 0xFF);
  // Fixed-length FIFO mode with sync/CRC/whitening disabled. Software will scan
  // all byte/bit offsets, matching the rfcat/YardStick abstraction under test.
  this->write_register_(CC1101_PKTCTRL1, 0x00);
  this->write_register_(CC1101_PKTCTRL0, 0x00);
  this->write_register_(CC1101_FSCTRL1, 0x06);
  this->write_register_(CC1101_FSCTRL0, 0x00);
  this->write_register_(CC1101_FREQ2, static_cast<uint8_t>((frequency_word >> 16) & 0xFF));
  this->write_register_(CC1101_FREQ1, static_cast<uint8_t>((frequency_word >> 8) & 0xFF));
  this->write_register_(CC1101_FREQ0, static_cast<uint8_t>(frequency_word & 0xFF));
  this->write_register_(CC1101_MDMCFG4, mdmcfg4);
  this->write_register_(CC1101_MDMCFG3, mdmcfg3);
  this->write_register_(CC1101_MDMCFG2, 0x30);
  this->write_register_(CC1101_MDMCFG1, 0x00);
  this->write_register_(CC1101_MDMCFG0, 0xF8);
  this->write_register_(CC1101_DEVIATN, 0x00);
  this->write_register_(CC1101_MCSM1, 0x30);
  this->write_register_(CC1101_MCSM0, 0x18);
  this->write_register_(CC1101_FOCCFG, 0x16);
  this->write_register_(CC1101_BSCFG, 0x6C);
  this->write_register_(CC1101_AGCCTRL2, 0x43);
  this->write_register_(CC1101_AGCCTRL1, 0x40);
  this->write_register_(CC1101_AGCCTRL0, 0x91);
  this->write_register_(CC1101_FREND1, 0x56);
  this->write_register_(CC1101_FREND0, 0x11);
  this->write_register_(CC1101_FSCAL3, 0xE9);
  this->write_register_(CC1101_FSCAL2, 0x2A);
  this->write_register_(CC1101_FSCAL1, 0x00);
  this->write_register_(CC1101_FSCAL0, 0x1F);
  this->write_register_(CC1101_TEST2, 0x81);
  this->write_register_(CC1101_TEST1, 0x35);
  this->write_register_(CC1101_TEST0, 0x09);

  result.mdmcfg4 = this->read_register_(CC1101_MDMCFG4);
  result.mdmcfg3 = this->read_register_(CC1101_MDMCFG3);
  result.mdmcfg2 = this->read_register_(CC1101_MDMCFG2);
  result.pktctrl1 = this->read_register_(CC1101_PKTCTRL1);
  result.pktctrl0 = this->read_register_(CC1101_PKTCTRL0);
  result.sync1 = this->read_register_(CC1101_SYNC1);
  result.sync0 = this->read_register_(CC1101_SYNC0);
  result.agcctrl2 = this->read_register_(CC1101_AGCCTRL2);
  result.agcctrl1 = this->read_register_(CC1101_AGCCTRL1);
  result.agcctrl0 = this->read_register_(CC1101_AGCCTRL0);

  this->strobe_(CC1101_SCAL);
  esp_rom_delay_us(1000);
  result.marcstate_after_config = this->read_marcstate();
  this->strobe_(CC1101_SFRX);
  this->strobe_(CC1101_SRX);
  esp_rom_delay_us(1000);
  result.marcstate_after_rx = this->read_marcstate();

  const int64_t deadline_us = esp_timer_get_time() + static_cast<int64_t>(duration_ms) * 1000LL;
  while (esp_timer_get_time() < deadline_us) {
    result.poll_count++;
    const uint8_t rxbytes_status = this->read_status_register_(CC1101_RXBYTES);
    const uint8_t fifo_count = static_cast<uint8_t>(rxbytes_status & 0x7FU);
    result.rxbytes_max = std::max(result.rxbytes_max, fifo_count);
    if ((rxbytes_status & 0x80U) != 0U) {
      result.rx_fifo_overflow = true;
      break;
    }
    if (fifo_count > 0U) {
      const size_t remaining = result.bytes.size() - result.byte_count;
      const size_t drain_count = std::min<size_t>(fifo_count, remaining);
      if (drain_count > 0U) {
        this->read_burst_register_(CC1101_RXFIFO, result.bytes.data() + result.byte_count, drain_count);
        result.byte_count = static_cast<uint16_t>(result.byte_count + drain_count);
      }
      if (drain_count < fifo_count || result.byte_count >= result.bytes.size()) {
        result.buffer_full = true;
        break;
      }
    }
    esp_rom_delay_us(1000);
  }

  result.rssi_raw = this->read_status_register_(CC1101_RSSI);
  result.lqi_raw = this->read_status_register_(CC1101_LQI);
  result.pktstatus = this->read_status_register_(CC1101_PKTSTATUS);
  result.rxbytes_final = static_cast<uint8_t>(this->read_status_register_(CC1101_RXBYTES) & 0x7FU);
  this->strobe_(CC1101_SIDLE);
  result.marcstate_after_idle = this->read_marcstate();
  this->strobe_(CC1101_SFRX);
  result.completed_tick_ms = static_cast<uint32_t>(esp_timer_get_time() / 1000ULL);
  result.elapsed_ms = result.completed_tick_ms >= result.started_tick_ms
                          ? result.completed_tick_ms - result.started_tick_ms
                          : duration_ms;
  error.clear();
  return true;
}

} // namespace proflame2_tembed
} // namespace esphome
