#pragma once

#include "esphome/core/defines.h"

#ifdef USE_PROFLAME2_GPIO_EDGE_DIAGNOSTICS
#ifndef USE_PROFLAME2_WAVEFORM_DIAGNOSTICS
#error "GPIO edge diagnostics requires waveform diagnostics"
#endif

#include <cstddef>
#include <cstdint>
#include <string>
#include "esp_attr.h"
#include "esphome/core/gpio.h"
#include "freertos/FreeRTOS.h"

namespace esphome {
namespace proflame2_tembed {

// ISR service times, not calibrated physical edge times. Never drive this pin.
class GPIOEdgeObserver {
public:
  static constexpr size_t CAPACITY = 2048;
  static constexpr uint32_t IRQ_BUDGET = 32;
  static constexpr uint32_t IRQ_WINDOW_US = 1000;
  bool begin(InternalGPIOPin* pin, std::string& error);
  bool end(std::string& error);
  void snapshot(int64_t rmt_arm_us);
  int64_t snapshot_for_burst(int64_t arm_us, int64_t seal_us, bool& observed);
  bool export_snapshot(uint32_t session, uint32_t capture);

  struct Ring {
    uint64_t edges[CAPACITY]{};
    uint64_t total{0};
    int64_t started_us{0};
    int64_t ready_us{0};
    int64_t budget_start_us{0};
    uint32_t budget_count{0};
    uint32_t repeated_levels{0};
    int gpio_num{-1};
    bool last_level{false};
    bool active{false};
    bool storm{false};
    bool IRAM_ATTR record(int64_t now, bool level) {
      if (!active)
        return false;
      if (level == last_level)
        repeated_levels++;
      last_level = level;
      edges[total & (CAPACITY - 1)] = (static_cast<uint64_t>(now) << 1U) | level;
      total++;
      if (now - budget_start_us >= IRQ_WINDOW_US) {
        budget_start_us = now;
        budget_count = 0;
      }
      if (++budget_count > IRQ_BUDGET) {
        storm = true;
        active = false;
        return true;
      }
      return false;
    }
  };

  struct Snapshot {
    uint64_t edges[CAPACITY]{};
    size_t count{0};
    uint64_t first_seq{0};
    uint64_t total{0};
    int64_t requested_us{0};
    int64_t snapshot_us{0};
    int64_t started_us{0};
    int64_t ready_us{0};
    uint32_t repeated_levels{0};
    bool clipped{false};
    bool raced{false};
    bool storm{false};
    uint32_t checksum() const {
      uint32_t hash = 2166136261U;
      for (size_t index = 0; index < count; index++)
        for (unsigned shift = 0; shift < 64; shift += 8)
          hash = (hash ^ ((edges[index] >> shift) & 255U)) * 16777619U;
      return hash;
    }
  };

  const Snapshot& saved_snapshot() const {
    return this->context_->saved;
  }
  void reset_export() {
    this->export_phase_ = 0;
    this->export_offset_ = 0;
  }
  bool export_snapshot(uint32_t session, uint32_t capture, const Snapshot& saved);

private:
  struct Context {
    portMUX_TYPE mux = portMUX_INITIALIZER_UNLOCKED;
    Ring ring{};
    Snapshot saved{};
  };
  Context* context_{nullptr};
  bool handler_attached_{false};
  bool needs_quiesce_{false};
  uint8_t export_phase_{0};
  size_t export_offset_{0};
  static void IRAM_ATTR on_edge_(void* arg);
  static void quiesce_(void*) {}
};

} // namespace proflame2_tembed
} // namespace esphome
#endif
