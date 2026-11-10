#pragma once
#include "esphome/core/test_clock.h"
inline uint32_t esp_rom_get_cpu_ticks_per_us() {
  return 1;
}
inline void esp_rom_delay_us(uint32_t duration) {
  test_clock_us += duration;
}
