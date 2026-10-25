#include "radio_cc1101.h"
#include "radio_trace.h"
#include "esphome/core/log.h"
#include <cassert>

using namespace esphome;
using namespace esphome::proflame2_tembed;

// Other radio.cpp methods refer to the production TX pin accessor.
GPIOPin* RadioCC1101::async_tx_pin_() const { return nullptr; }

class TraceRadio : public RadioCC1101 {
public:
  void initialized(bool value) { initialized_ = value; }
};

int main() {
  TraceRadio radio;
  radio.trace_radio_registers("radio_initialized", "-");
  assert(spi::commands.empty() && trace_logs.empty());
  radio.initialized(true);
  for (size_t i = 0; i < spi::registers.size(); i++) {
    spi::registers[i] = static_cast<uint8_t>(i);
  }
  spi::pa = {0, 0xC6, 0xC6, 0xC6, 0xC6, 0xC6, 0xC6, 0xC6};
  radio.trace_radio_registers("tx_prepared", "proflame2-48");
#if PROFLAME2_RADIO_TRACE
  assert(spi::commands.size() == 58);
  for (size_t i = 0; i < 47; i++) {
    assert(spi::commands[i] == (i | 0x80));
  }
  for (size_t i = 0; i < RadioTraceSnapshot::STATUS_ADDRESSES.size(); i++) {
    assert(spi::commands[47 + i] == (RadioTraceSnapshot::STATUS_ADDRESSES[i] | 0xC0));
  }
  assert(spi::commands.back() == 0xFE);
  assert(trace_logs.size() == 18);
  assert(trace_logs[0].find("seq=1 stage=tx_prepared request=proflame2-48") != std::string::npos);
  assert(trace_logs[1].find("config[00]=00 01 02 03 04 05 06 07") != std::string::npos);
  assert(trace_logs[6].find("config[28]=28 29 2A 2B 2C 2D 2E") != std::string::npos);
  assert(trace_logs.back().find("pa=00 C6 C6 C6 C6 C6 C6 C6") != std::string::npos);
  for (const auto& line : trace_logs) {
    assert(line.size() < 200);
  }
  spi::registers[0x0D] = 0xAA;
  radio.trace_radio_registers("tx_complete", "proflame2-48");
  assert(trace_logs[18].find("seq=2 stage=tx_complete") != std::string::npos);
  assert(trace_logs[20].find("AA") != std::string::npos);
#else
  assert(spi::commands.empty() && trace_logs.empty());
#endif
}
