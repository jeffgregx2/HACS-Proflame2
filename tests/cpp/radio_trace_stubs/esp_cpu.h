#pragma once
#include "esphome/core/test_clock.h"
inline uint32_t esp_cpu_get_cycle_count() {
  return static_cast<uint32_t>(++test_clock_us);
}
