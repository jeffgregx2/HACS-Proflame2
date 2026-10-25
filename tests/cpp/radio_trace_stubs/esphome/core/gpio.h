#pragma once
#include "test_clock.h"
#include <utility>
#include <vector>
namespace esphome {
inline std::vector<std::pair<int64_t, bool>> test_pin_edges;
class GPIOPin {
public:
  void setup() {}
  bool digital_read() {
    return test_clock_us < ready_at_us;
  }
  int64_t ready_at_us{0};
  bool is_internal() const {
    return true;
  }
  void digital_write(bool value) {
    if (level_ != value) {
      test_pin_edges.emplace_back(test_clock_us, value);
    }
    level_ = value;
  }

private:
  bool level_{false};
};
class InternalGPIOPin : public GPIOPin {
public:
  int get_pin() const {
    return 3;
  }
};
} // namespace esphome
