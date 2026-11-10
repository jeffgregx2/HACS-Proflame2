// Execute the actual firmware TX implementation against a deterministic clock.
// This validates software scheduling, not CC1101 analog behavior or RF timing.
#include "radio_cc1101.h"
#include "esphome/core/log.h"
#include <cassert>
#include <string>
#include <vector>

using namespace esphome;
using namespace esphome::proflame2_tembed;

class BenchRadio : public RadioCC1101 {
public:
  void prepare(GPIOPin* pin) {
    initialized_ = true;
    data_rate_bps_ = 2400;
    gdo0_pin_ = pin;
  }
};

struct RunResult {
  std::vector<std::pair<int64_t, bool>> edges;
  TXTimingDiagnostics timing;
};

static RunResult run(const std::vector<uint8_t>& payload, uint32_t bits, uint32_t delay_us,
                     NativeGroupRepeatBoundaryMode boundary, uint32_t startup_us = 750, bool never_ready = false,
                     uint8_t repeat_count = 5) {
  test_clock_us = 0;
  test_pin_edges.clear();
  spi::commands.clear();
  spi::registers.fill(0);
  spi::allow_radio_operations = true;
  spi::stx_count = 0;
  spi::stx_times.clear();
  spi::tx_startup_delay_us = startup_us;
  spi::tx_never_ready = never_ready;
  trace_logs.clear();
  InternalGPIOPin pin;
  BenchRadio radio;
  radio.prepare(&pin);
#if PROFLAME2_RADIO_TRACE
  radio.set_radio_trace_enabled(false);
#endif
  radio.set_pre_tx_settle_delay_us(delay_us);
  trace_logs.clear();
  uint32_t elapsed_ms = 0;
  TXTimingDiagnostics timing;
  std::string error;
  const bool ok =
      radio.transmit_async_ook(payload.data(), payload.size(), bits, repeat_count, 0, TXMode::PROFLAME_NATIVE_GROUPS,
                               NativeGroupTimingProfile::NATIVE_REMOTE, boundary, 834, 0, 0, elapsed_ms, timing, error,
                               radio.get_pre_tx_settle_delay_us());
  assert(ok && error.empty());
  assert(!test_pin_edges.empty() && test_pin_edges.front().second);
  assert(test_pin_edges.front().first >= spi::stx_times.front() + (never_ready ? 10000 : startup_us) + delay_us);
  assert(spi::stx_count == (boundary == NativeGroupRepeatBoundaryMode::REENTER_TX ? repeat_count : 1U));
  assert(timing.payload_bits == bits);
  assert(timing.inter_repeat_gap_samples == repeat_count - 1U);
  if (never_ready) {
    assert(!trace_logs.empty());
    assert(trace_logs.front().find("TX readiness timeout after 10000 us") != std::string::npos);
  } else {
    assert(trace_logs.empty()); // Normal readiness adds no logging in the TX loop.
  }
  return {test_pin_edges, timing};
}

int main(int argc, char** argv) {
  assert(argc == 3);
  const std::string hex = argv[1];
  std::vector<uint8_t> payload;
  for (size_t i = 0; i < hex.size(); i += 2) {
    payload.push_back(static_cast<uint8_t>(std::stoul(hex.substr(i, 2), nullptr, 16)));
  }
  const uint32_t bits = static_cast<uint32_t>(std::stoul(argv[2]));
  assert(bits == 182 || bits == 260);
  for (const auto boundary :
       {NativeGroupRepeatBoundaryMode::CONTINUOUS_TX, NativeGroupRepeatBoundaryMode::REENTER_TX}) {
    const auto baseline = run(payload, bits, 0, boundary);
    for (const uint32_t delay : {100U, 1000U, 20000U, 100000U}) {
      const auto delayed = run(payload, bits, delay, boundary);
      assert(delayed.edges.size() == baseline.edges.size());
      assert(delayed.edges.front().first >= baseline.edges.front().first);
      const int64_t added_us = delayed.edges.front().first - baseline.edges.front().first;
      assert(added_us >= delay - 2 && added_us <= delay + 2);
      for (size_t i = 0; i < baseline.edges.size(); i++) {
        assert(delayed.edges[i].second == baseline.edges[i].second);
        assert(delayed.edges[i].first - delayed.edges.front().first ==
               baseline.edges[i].first - baseline.edges.front().first);
      }
      assert(delayed.timing.inter_repeat_gap_min_us == baseline.timing.inter_repeat_gap_min_us);
      assert(delayed.timing.inter_repeat_gap_max_us == baseline.timing.inter_repeat_gap_max_us);
    }
    const auto reset = run(payload, bits, 0, boundary);
    assert(reset.edges == baseline.edges);
    for (const uint8_t copies : {8, 10}) {
      const auto longer = run(payload, bits, 0, boundary, 750, false, copies);
      assert(baseline.edges.size() % 5 == 0);
      assert(longer.edges.size() == baseline.edges.size() / 5 * copies);
      for (size_t i = 0; i < baseline.edges.size(); ++i) {
        assert(longer.edges[i] == baseline.edges[i]);
      }
      assert(longer.timing.inter_repeat_gap_min_us == baseline.timing.inter_repeat_gap_min_us);
      assert(longer.timing.inter_repeat_gap_max_us == baseline.timing.inter_repeat_gap_max_us);
    }
    // Startup beyond the old 1.5 ms timeout now waits for confirmed TX.
    run(payload, bits, 0, boundary, 3000);
    run(payload, bits, 20000, boundary, 9000);
    // Never-ready still emits the complete legacy/extended waveform after a
    // bounded 10 ms wait, with an explicit warning instead of silent success.
    const auto fallback = run(payload, bits, 20000, boundary, 0, true);
    assert(fallback.edges.size() == baseline.edges.size());
  }
  // A nonzero setting must not be silently ignored by experimental TX modes.
  test_pin_edges.clear();
  spi::stx_count = 0;
  InternalGPIOPin pin;
  BenchRadio radio;
  radio.prepare(&pin);
  uint32_t elapsed_ms = 0;
  TXTimingDiagnostics timing;
  std::string error;
  assert(!radio.transmit_async_ook(
      payload.data(), payload.size(), bits, 5, 0, TXMode::CONTINUOUS_BURST, NativeGroupTimingProfile::NATIVE_REMOTE,
      NativeGroupRepeatBoundaryMode::CONTINUOUS_TX, 834, 0, 0, elapsed_ms, timing, error, 20000));
  assert(error == "pre_tx_settle_requires_native_groups");
  assert(spi::stx_count == 0 && test_pin_edges.empty());
}
