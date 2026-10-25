#pragma once
#include <array>
#include <cassert>
#include <cstddef>
#include <cstdint>
#include <vector>

namespace esphome {
namespace spi {
enum { BIT_ORDER_MSB_FIRST, CLOCK_POLARITY_LOW, CLOCK_PHASE_LEADING, DATA_RATE_4MHZ };
inline std::vector<uint8_t> commands;
inline std::array<uint8_t, 64> registers{};
inline std::array<uint8_t, 8> pa{};
template<int, int, int, int> class SPIDevice {
public:
  void enable() { command_pending_ = true; }
  void disable() {}
  void write_byte(uint8_t value) {
    assert(command_pending_); // Snapshot must never write register data.
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
    return registers[command_ & 0x3F];
  }
  void write_array(const uint8_t*, size_t) { assert(false); }
  uint8_t transfer_byte(uint8_t) {
    assert(false); // No reset, calibration, FIFO flush or state-change strobes.
    return 0;
  }
private:
  bool command_pending_{false};
  uint8_t command_{0};
  size_t index_{0};
};
} // namespace spi
} // namespace esphome
