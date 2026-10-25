#pragma once
#include <array>
#include <cassert>
#include <cstddef>
#include <cstdint>
#include <vector>
#include "esphome/core/test_clock.h"

namespace esphome {
namespace spi {
enum { BIT_ORDER_MSB_FIRST, CLOCK_POLARITY_LOW, CLOCK_PHASE_LEADING, DATA_RATE_4MHZ };
inline std::vector<uint8_t> commands;
inline std::array<uint8_t, 64> registers{};
inline std::array<uint8_t, 8> pa{};
inline bool allow_radio_operations = false;
inline int64_t last_stx_us = 0;
inline int64_t ready_at_us = 0;
inline uint32_t stx_count = 0;
inline std::vector<int64_t> stx_times;
template <int, int, int, int> class SPIDevice {
public:
  void enable() {
    command_pending_ = true;
  }
  void disable() {}
  void write_byte(uint8_t value) {
    if (!command_pending_) {
      assert(allow_radio_operations);
      registers[command_ & 0x3F] = value;
      return;
    }
    command_pending_ = false;
    command_ = value;
    index_ = 0;
    commands.push_back(value);
  }
  uint8_t read_byte() {
    assert(command_ & 0x80);
    if ((command_ & 0x3F) == 0x3E) {
      assert(command_ == 0xFE && index_ < pa.size());
      return pa[index_++];
    }
    if (allow_radio_operations && (command_ & 0x3F) == 0x35 && registers[0x35] == 0x13 && test_clock_us < ready_at_us) {
      return 0x08; // Simulated startup: not TX-ready until 750 us after STX.
    }
    return registers[command_ & 0x3F];
  }
  void write_array(const uint8_t* data, size_t length) {
    assert(allow_radio_operations && (command_ & 0x3F) == 0x3E && length == pa.size());
    for (size_t i = 0; i < length; i++) {
      pa[i] = data[i];
    }
  }
  uint8_t transfer_byte(uint8_t command) {
    assert(allow_radio_operations); // Snapshot-only tests reject all strobes.
    if (command == 0x35) {          // STX
      last_stx_us = test_clock_us;
      ready_at_us = test_clock_us + 750;
      registers[0x35] = 0x13;
      stx_count++;
      stx_times.push_back(test_clock_us);
    } else if (command == 0x36 || command == 0x30) { // SIDLE / SRES
      registers[0x35] = 0x01;
    }
    return 0;
  }

private:
  bool command_pending_{false};
  uint8_t command_{0};
  size_t index_{0};
};
} // namespace spi
} // namespace esphome
