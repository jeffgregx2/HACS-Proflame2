#pragma once
#include "esphome/core/test_clock.h"
inline int64_t esp_timer_get_time() {
  return ++test_clock_us;
}
