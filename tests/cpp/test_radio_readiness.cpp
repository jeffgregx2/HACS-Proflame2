#include "radio_cc1101.h"
#include "esphome/core/log.h"
#include <cassert>
#include <limits>

using namespace esphome;
using namespace esphome::proflame2_tembed;

GPIOPin* RadioCC1101::async_tx_pin_() const {
  return nullptr;
}
class BenchRadio : public RadioCC1101 {
public:
  using RadioCC1101::read_burst_register_;
  using RadioCC1101::read_register_;
  using RadioCC1101::strobe_;
  using RadioCC1101::write_burst_register_;
  using RadioCC1101::write_register_;
};

static void reset() {
  test_clock_us = 0;
  spi::allow_radio_operations = true;
  spi::registers[0x35] = 0x0D;
  spi::idle_delay_us = 0;
  spi::idle_failures = 0;
  spi::idle_never_ready = false;
  spi::strobes.clear();
  spi::header_times.clear();
  spi::marcstate_script.clear();
  spi::marcstate_script_index = 0;
  trace_logs.clear();
}

int main() {
  BenchRadio radio;
  assert(radio.get_radio_ready_timeout_us() == 10000);
  reset();
  spi::idle_delay_us = 3000;
  radio.set_idle();
  assert(spi::strobes.size() == 2);
  assert(spi::strobes[0].second == CC1101_SIDLE);
  assert(spi::strobes[1].second == CC1101_SFTX);
  assert(spi::strobes[1].first - spi::strobes[0].first >= 3000);
  assert(trace_logs.empty());

  reset();
  spi::marcstate_script = {0x01, 0x0D, 0x01, 0x01};
  radio.set_idle();
  assert(spi::marcstate_script_index == 4);
  assert(spi::strobes.back().first >= 30); // One transient IDLE read is not enough.

  reset();
  radio.set_radio_ready_timeout_us(100);
  spi::idle_failures = 1;
  radio.set_idle();
  assert(spi::strobes.size() == 3);
  assert(spi::strobes[1].second == CC1101_SIDLE);
  assert(spi::strobes[2].second == CC1101_SFTX);
  assert(trace_logs.size() == 1);

  reset();
  spi::idle_never_ready = true;
  radio.set_idle();
  assert(spi::strobes.size() == 3 && spi::strobes.back().second == CC1101_SFTX);
  assert(trace_logs.size() == 2);
  assert(test_clock_us >= 200 && test_clock_us < 300);
  assert(trace_logs.back().find("proceeding best-effort") != std::string::npos);

  // All six SPI access forms check SO after CS and before sending a header.
  GPIOPin miso;
  radio.set_spi_miso_pin(&miso);
  for (int operation = 0; operation < 6; ++operation) {
    reset();
    miso.ready_at_us = 50;
    uint8_t data[8]{};
    switch (operation) {
    case 0:
      radio.write_register_(0x0D, 0x55);
      break;
    case 1:
      radio.read_register_(0x0D);
      break;
    case 2:
      radio.read_marcstate();
      break;
    case 3:
      radio.read_burst_register_(CC1101_PATABLE, data, 8);
      break;
    case 4:
      radio.write_burst_register_(CC1101_PATABLE, data, 8);
      break;
    case 5:
      radio.strobe_(CC1101_STX);
      break;
    }
    assert(test_clock_us >= 50 && trace_logs.empty());
    if (operation == 5) {
      assert(spi::strobes.front().first >= 50);
    } else {
      assert(spi::header_times.front() >= 50);
    }
  }
  reset();
  miso.ready_at_us = std::numeric_limits<int64_t>::max();
  radio.write_register_(0x0D, 0x33);
  assert(test_clock_us >= 100 && test_clock_us < 130);
  assert(spi::registers[0x0D] == 0x33); // Best-effort fallback still executes.
  assert(trace_logs.size() == 1);
  radio.read_register_(0x0D);
  assert(trace_logs.size() == 1); // Persistent fault does not flood the logger.
  miso.ready_at_us = 0;
  radio.read_register_(0x0D);
  miso.ready_at_us = std::numeric_limits<int64_t>::max();
  radio.read_register_(0x0D);
  assert(trace_logs.size() == 2); // A distinct recurrence is reported.
}
