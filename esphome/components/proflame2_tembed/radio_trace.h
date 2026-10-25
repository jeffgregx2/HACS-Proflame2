#pragma once

#include <array>
#include <cstddef>
#include <cstdint>

namespace esphome {
namespace proflame2_tembed {

struct RadioTraceSnapshot {
  std::array<uint8_t, 47> config{};
  // PARTNUM, VERSION, FREQEST, LQI, RSSI, MARCSTATE, PKTSTATUS,
  // VCO_VC_DAC, TXBYTES, RXBYTES. Never read FIFO or issue strobes.
  static constexpr std::array<uint8_t, 10> STATUS_ADDRESSES{
      0x30, 0x31, 0x32, 0x33, 0x34, 0x35, 0x38, 0x39, 0x3A, 0x3B};
  std::array<uint8_t, STATUS_ADDRESSES.size()> status{};
  std::array<uint8_t, 8> pa{};
};

// Value-owned records can cross the existing FreeRTOS completion queue safely.
struct RadioTraceRecord {
  RadioTraceSnapshot snapshot{};
  uint32_t sequence{0};
  uint32_t uptime_ms{0};
  uint32_t read_us{0};
};

// Capture before logging so log transport does not separate individual reads.
// Live status registers are sequential observations, not an atomic snapshot.
template<typename ReadConfig, typename ReadStatus, typename ReadBurst>
RadioTraceSnapshot capture_radio_trace(ReadConfig read_config, ReadStatus read_status, ReadBurst read_burst) {
  RadioTraceSnapshot result;
  for (size_t address = 0; address < result.config.size(); address++) {
    result.config[address] = read_config(static_cast<uint8_t>(address));
  }
  for (size_t index = 0; index < result.status.size(); index++) {
    result.status[index] = read_status(RadioTraceSnapshot::STATUS_ADDRESSES[index]);
  }
  read_burst(0x3E, result.pa.data(), result.pa.size());
  return result;
}

} // namespace proflame2_tembed
} // namespace esphome
