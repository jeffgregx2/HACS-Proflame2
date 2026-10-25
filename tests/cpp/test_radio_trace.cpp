#include "radio_cc1101.h"
#include "radio_trace.h"
#include "esphome/core/log.h"
#include <cassert>
#include <type_traits>

using namespace esphome;
using namespace esphome::proflame2_tembed;

// Other radio.cpp methods refer to the production TX pin accessor.
GPIOPin* RadioCC1101::async_tx_pin_() const {
  return nullptr;
}

class TraceRadio : public RadioCC1101 {
public:
  void initialized(bool value) {
    initialized_ = value;
  }
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
  assert(trace_logs.size() == 19);
  assert(trace_logs[0].find("seq=1 stage=tx_prepared request=proflame2-48") != std::string::npos);
  assert(trace_logs[1].find("config[00]=00 01 02 03 04 05 06 07") != std::string::npos);
  assert(trace_logs[6].find("config[28]=28 29 2A 2B 2C 2D 2E") != std::string::npos);
  assert(trace_logs[17].find("pa=00 C6 C6 C6 C6 C6 C6 C6") != std::string::npos);
  assert(trace_logs.back() == "RFTRACE seq=1 end=1");
  for (const auto& line : trace_logs) {
    assert(line.size() < 200);
  }
  spi::registers[0x0D] = 0xAA;
  radio.trace_radio_registers("tx_complete", "proflame2-48");
  assert(trace_logs[19].find("seq=2 stage=tx_complete") != std::string::npos);
  assert(trace_logs[21].find("AA") != std::string::npos);

  static_assert(std::is_trivially_copyable<RadioTraceRecord>::value);
  trace_logs.clear();
  spi::commands.clear();
  // Simulate the worker capturing before/after TX, then copying its event.
  const auto before = radio.capture_radio_registers();
  spi::registers[0x0D] = 0xBB;
  spi::registers[0x35] = 0x13;
  spi::pa[1] = 0x55;
  const auto after = radio.capture_radio_registers();
  auto copied_before = before;
  auto copied_after = after;
  assert(trace_logs.empty()); // No worker-task logger traffic.
  assert(spi::commands.size() == 116);
  // RX restoration changes the live radio, but must not alter queued records.
  spi::registers.fill(0xCC);
  spi::pa.fill(0xCC);
  spi::commands.clear();
  radio.log_radio_registers(copied_before, "tx_prepared", "proflame2-49");
  radio.log_radio_registers(copied_after, "tx_complete", "proflame2-49");
  assert(spi::commands.empty()); // Deferred output never re-reads the radio.
  assert(trace_logs.size() == 38);
  assert(trace_logs[0].find("seq=3 stage=tx_prepared request=proflame2-49") != std::string::npos);
  assert(trace_logs[0].find("uptime_ms=1000 read_us=1") != std::string::npos);
  assert(trace_logs[2].find("AA") != std::string::npos);
  assert(trace_logs[19].find("seq=4 stage=tx_complete request=proflame2-49") != std::string::npos);
  assert(trace_logs[21].find("BB") != std::string::npos);
  assert(trace_logs[31].find("status[35]=13") != std::string::npos);
  assert(trace_logs[36].find("pa=00 55 C6 C6 C6 C6 C6 C6") != std::string::npos);
  assert(trace_logs[37] == "RFTRACE seq=4 end=1");
  for (const auto& line : trace_logs) {
    assert(line.size() < 200);
  }
  trace_logs.clear();
  radio.log_radio_registers(RadioTraceRecord{}, "tx_complete", "uninitialized");
  assert(trace_logs.empty() && spi::commands.empty());

  assert(radio.is_radio_trace_enabled());
  assert(radio.get_pre_tx_settle_delay_us() == 0);
  radio.set_radio_trace_enabled(false);
  trace_logs.clear(); // The one-time setting-change acknowledgment is expected.
  const auto disabled = radio.capture_radio_registers();
  radio.trace_radio_registers("rmt_rx_running", "disabled");
  radio.log_radio_registers(copied_after, "tx_complete", "queued-before-disable");
  assert(disabled.sequence == 0);
  assert(trace_logs.empty() && spi::commands.empty());
  radio.set_pre_tx_settle_delay_us(20000);
  assert(radio.get_pre_tx_settle_delay_us() == 20000);
  radio.set_pre_tx_settle_delay_us(999999);
  assert(radio.get_pre_tx_settle_delay_us() == 100000);
  radio.set_pre_tx_settle_delay_us(0);
  assert(radio.get_pre_tx_settle_delay_us() == 0);
  radio.set_radio_trace_enabled(true);
  trace_logs.clear();
  radio.trace_radio_registers("rmt_rx_running", "reenabled");
  assert(spi::commands.size() == 58);
  assert(trace_logs[0].find("seq=5 stage=rmt_rx_running") != std::string::npos);
  assert(trace_logs.back() == "RFTRACE seq=5 end=1");

  assert(radio_trace_pre_tx_deadline(1000, 500, 0, 0) == 1000);
  assert(radio_trace_pre_tx_deadline(1000, 500, 0, 20000) == 21000);
  assert(radio_trace_pre_tx_deadline(30000, 500, 0, 20000) == 50000);
  assert(radio_trace_pre_tx_deadline(100, 500, 0, 20000) == 20500);
  for (uint8_t repeat = 1; repeat < 5; repeat++) {
    assert(radio_trace_pre_tx_deadline(1000, 500, repeat, 20000) == 1000);
  }
#else
  assert(spi::commands.empty() && trace_logs.empty());
#endif
}
