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
inline std::vector<int64_t> header_times;
inline std::array<uint8_t, 64> registers{};
inline std::array<uint8_t, 8> pa{};
inline bool allow_radio_operations = false;
inline int64_t last_stx_us = 0;
inline int64_t ready_at_us = 0;
inline uint32_t stx_count = 0;
inline std::vector<int64_t> stx_times;
inline uint32_t tx_startup_delay_us = 750;
inline bool tx_never_ready = false;
inline uint32_t idle_delay_us = 0;
inline uint32_t idle_failures = 0;
inline bool idle_never_ready = false;
inline int64_t idle_ready_at_us = 0;
inline std::vector<std::pair<int64_t, uint8_t>> strobes;
inline std::vector<uint8_t> marcstate_script;
inline size_t marcstate_script_index = 0;
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
    header_times.push_back(test_clock_us);
  }
  uint8_t read_byte() {
    assert(command_ & 0x80);
    if ((command_ & 0x3F) == 0x3E) {
      assert(command_ == 0xFE && index_ < pa.size());
      return pa[index_++];
    }
    if (allow_radio_operations && (command_ & 0x3F) == 0x35) {
      if (marcstate_script_index < marcstate_script.size()) {
        return marcstate_script[marcstate_script_index++];
      }
      if (registers[0x35] == 0x13 && (tx_never_ready || test_clock_us < ready_at_us)) {
        return 0x08;
      }
      if (registers[0x35] == 0x01 && test_clock_us < idle_ready_at_us) {
        return 0x0D;
      }
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
    strobes.emplace_back(test_clock_us, command);
    if (command == 0x35) {          // STX
      last_stx_us = test_clock_us;
      ready_at_us = test_clock_us + tx_startup_delay_us;
      registers[0x35] = 0x13;
      stx_count++;
      stx_times.push_back(test_clock_us);
    } else if (command == 0x36) { // SIDLE
      if (!idle_never_ready && idle_failures == 0) {
        registers[0x35] = 0x01;
        idle_ready_at_us = test_clock_us + idle_delay_us;
      } else if (idle_failures > 0) {
        --idle_failures;
      }
    } else if (command == 0x30) { // SRES
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
