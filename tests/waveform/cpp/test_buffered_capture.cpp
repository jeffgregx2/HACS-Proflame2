#include "buffered_capture_runtime.h"
#include <cassert>
#include <iostream>
#include "driver/gpio.h"
#define private public
#include "diagnostics/buffered_ook_capture.h"
#undef private

using namespace esphome::proflame2_tembed;

template <typename Predicate> void wait_for(Predicate predicate) {
  for (int i = 0; i < 3000; i++) {
    if (predicate())
      return;
    std::this_thread::sleep_for(std::chrono::milliseconds(1));
  }
  assert(false && "task/driver did not reach expected state");
}

int main(int argc, char** argv) {
  assert(argc == 2);
  const std::string mode = argv[1];
  BufferedOokCapture capture;
  RawCaptureLimits limits;
  limits.session_ms = 90000;
  limits.max_exports = mode == "limit" || mode == "retention_loss" ? 1 : 8;
  limits.capture_rejected = mode == "partial" || mode == "timeout_partial";
  capture.set_limits(limits);
#ifdef USE_PROFLAME2_GPIO_EDGE_DIAGNOSTICS
  esphome::InternalGPIOPin pin;
  capture.set_pin(&pin);
#endif
  size_t count;
  std::cin >> count;
  std::vector<rmt_symbol_word_t> symbols(count);
  for (auto& s : symbols) {
    uint32_t word;
    std::cin >> std::hex >> word;
    s.duration0 = word & 32767;
    s.level0 = (word >> 15) & 1;
    s.duration1 = (word >> 16) & 32767;
    s.level1 = (word >> 31) & 1;
  }
  std::string error;
  if (mode.rfind("setup_fail_", 0) == 0) {
    fault = std::stoi(mode.substr(11));
    assert(!capture.begin(3, error));
    assert(!error.empty());
    assert(!capture.active());
    assert(allocations == 0);
    return 0;
  }
  assert(capture.begin(3, error));
  assert(capture.context_->task != nullptr);
  assert(!capture.begin(3, error) && !error.empty());
#ifdef USE_PROFLAME2_GPIO_EDGE_DIAGNOSTICS
  gpio_level = 0;
  gpio_handler(gpio_argument);
  mock_time += 40000;
#endif
  auto send = [&](bool final = true, size_t length = 0) {
    std::lock_guard<std::mutex> lock(driver_mutex);
    assert(driver_enabled);
#ifdef USE_PROFLAME2_GPIO_EDGE_DIAGNOSTICS
    const size_t total = length ? length : symbols.size();
    for (size_t index = 0; index < total; index++) {
      const auto& s = symbols[index];
      if (s.duration0) {
        gpio_level = s.level0;
        gpio_handler(gpio_argument);
        mock_time += s.duration0;
      }
      if (s.duration1) {
        gpio_level = s.level1;
        gpio_handler(gpio_argument);
        mock_time += s.duration1;
      }
    }
#endif
    rmt_rx_done_event_data_t event{symbols.data(), length ? length : symbols.size(), {final}};
    done_callback(nullptr, &event, done_argument);
  };
  auto wait_processed = [&] {
    wait_for([&] {
      capture.process();
      return capture.retained_ != 0 || capture.suppressed_ != 0;
    });
  };
  if (mode == "quiet") {
    capture.request_stop();
  } else if (mode == "deadline") {
    mock_time += 90000001;
  } else if (mode == "partial" || mode == "timeout_partial") {
    send(false, count / 2);
    if (mode == "timeout_partial")
      mock_time += 90000001;
    else
      capture.request_stop();
  } else if (mode == "callback_loss") {
    std::lock_guard<std::mutex> lock(driver_mutex);
    rmt_rx_done_event_data_t event{nullptr, 10, {true}};
    done_callback(nullptr, &event, done_argument);
  } else if (mode == "pool_loss" || mode == "retention_loss") {
    send();
    wait_for([&] { return receive_calls >= 2; });
    send(); // Main intentionally does not release the first slot.
    wait_for([&] {
      portENTER_CRITICAL(&capture.context_->mux);
      const bool lost = capture.context_->pool_losses != 0;
      portEXIT_CRITICAL(&capture.context_->mux);
      return lost;
    });
  } else if (mode == "rearm_fail") {
    fail_rearm = true;
    send();
  } else if (mode == "queue_loss") {
    fault = 31;
    send();
    wait_for([&] {
      portENTER_CRITICAL(&capture.context_->mux);
      const bool lost = capture.context_->queue_losses != 0;
      portEXIT_CRITICAL(&capture.context_->mux);
      return lost;
    });
    fault = 0;
  } else if (mode == "stop_fail") {
    fault = 7;
    send();
    std::this_thread::sleep_for(std::chrono::milliseconds(20));
    capture.process();
    assert(capture.active() && !capture.hardware_released_);
    fault = 0;
  } else {
    send();
    wait_for([&] { return receive_calls >= 2; });
    wait_processed();
    assert(capture.retained_ == 1);
    capture.export_tick();
    assert(capture.exported_ == 0); // No raw export while reception runs.
    if (mode != "limit") {
      send();
      wait_for([&] { return receive_calls >= 3; });
      wait_for([&] {
        capture.process();
        return capture.retained_ == 2;
      });
    }
    capture.request_stop();
  }
  if (mode == "delete_fail") {
    fault = 8;
    wait_for([&] {
      capture.process();
      return capture.stopped_;
    });
    assert(capture.active() && !capture.hardware_released_);
    fault = 0;
  }
  wait_for([&] {
    capture.process();
    return capture.stopped_ && capture.hardware_released_;
  });
  assert(!driver_enabled);
  if (mode == "normal") {
    assert(capture.records_[0].raw.words[0] ==
           RawOokWindow::pack(symbols[0].duration0, symbols[0].level0, symbols[0].duration1, symbols[0].level1));
    assert(capture.records_[0].raw.arm_us <= capture.records_[0].arm_after_us);
    assert(capture.records_[0].arm_after_us <= capture.records_[0].raw.seal_us);
#ifdef USE_PROFLAME2_GPIO_EDGE_DIAGNOSTICS
    assert(capture.records_[0].gpio.count != 0);
    assert(capture.records_[0].gpio_anchor_observed);
    assert(capture.records_[0].gpio.requested_us == capture.records_[0].gpio_anchor_us - 75000);
#endif
  }
  for (int i = 0; i < 30000 && capture.active(); i++)
    capture.export_tick();
  assert(!capture.active());
  assert(allocations == 0);
  assert(capture.records_ == nullptr && capture.dma_ == nullptr);
  if (mode == "restart") {
    const int previous_receives = receive_calls;
    assert(capture.begin(3, error));
    send();
    wait_for([&] { return receive_calls > previous_receives + 1; });
    wait_processed();
    capture.request_stop();
    wait_for([&] {
      capture.process();
      return capture.stopped_ && capture.hardware_released_;
    });
    for (int i = 0; i < 30000 && capture.active(); i++)
      capture.export_tick();
    assert(!capture.active() && allocations == 0);
  }
  return 0;
}
