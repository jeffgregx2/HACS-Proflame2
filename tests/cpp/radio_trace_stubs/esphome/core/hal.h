#pragma once
#include <cstdint>
namespace esphome {
inline uint32_t micros() {
  static uint32_t now = 0;
  return ++now;
}
inline uint32_t millis() {
  return 1000;
}
} // namespace esphome
