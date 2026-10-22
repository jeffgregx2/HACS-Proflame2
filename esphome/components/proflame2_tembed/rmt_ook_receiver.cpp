#include "rmt_ook_receiver.h"

#include <cstdio>

#include "esp_err.h"

namespace esphome {
namespace proflame2_tembed {

namespace {

constexpr uint32_t PCM_UNIT_US = 417U;
constexpr uint32_t MAX_SIGNAL_RUN_UNITS = 3U;
constexpr uint16_t MIN_FRAME_PCM_BITS = 64U;

void append_error_(std::string& error, const char* prefix, esp_err_t result) {
  char buffer[64];
  snprintf(buffer, sizeof(buffer), "%s:%s", prefix, esp_err_to_name(result));
  error = buffer;
}

}  // namespace

bool RmtOokReceiver::begin(int gpio_num, std::string& error) {
  this->end();
  rmt_rx_channel_config_t config{};
  config.gpio_num = static_cast<gpio_num_t>(gpio_num);
  config.clk_src = RMT_CLK_SRC_DEFAULT;
  config.resolution_hz = RESOLUTION_HZ;
  config.mem_block_symbols = 64;
  config.intr_priority = 0;
  config.flags.invert_in = false;
  config.flags.with_dma = false;
  config.flags.allow_pd = false;
  config.flags.io_loop_back = false;

  esp_err_t result = rmt_new_rx_channel(&config, &this->channel_);
  if (result != ESP_OK) {
    this->channel_ = nullptr;
    append_error_(error, "rmt_new_rx_channel", result);
    return false;
  }
  this->queue_ = xQueueCreate(1, sizeof(rmt_rx_done_event_data_t));
  if (this->queue_ == nullptr) {
    error = "rmt_queue_create_failed";
    this->end();
    return false;
  }
  rmt_rx_event_callbacks_t callbacks{};
  callbacks.on_recv_done = &RmtOokReceiver::on_receive_done_;
  result = rmt_rx_register_event_callbacks(this->channel_, &callbacks, this->queue_);
  if (result != ESP_OK) {
    append_error_(error, "rmt_rx_register_callbacks", result);
    this->end();
    return false;
  }
  result = rmt_enable(this->channel_);
  if (result != ESP_OK) {
    append_error_(error, "rmt_enable", result);
    this->end();
    return false;
  }
  return this->arm_(error);
}

void RmtOokReceiver::end() {
  if (this->channel_ != nullptr) {
    rmt_disable(this->channel_);
    rmt_del_channel(this->channel_);
    this->channel_ = nullptr;
  }
  if (this->queue_ != nullptr) {
    vQueueDelete(this->queue_);
    this->queue_ = nullptr;
  }
}

bool RmtOokReceiver::poll(RmtOokCapture& capture, std::string& error) {
  if (this->channel_ == nullptr || this->queue_ == nullptr) {
    error = "rmt_not_active";
    return false;
  }
  rmt_rx_done_event_data_t event{};
  if (xQueueReceive(this->queue_, &event, 0) != pdTRUE) {
    return false;
  }
  const bool converted = this->convert_symbols_(event.received_symbols, event.num_symbols, capture, error);
  std::string arm_error;
  if (!this->arm_(arm_error)) {
    error = arm_error;
    return false;
  }
  return converted;
}

bool RmtOokReceiver::on_receive_done_(rmt_channel_handle_t, const rmt_rx_done_event_data_t* event_data,
                                      void* user_data) {
  BaseType_t higher_priority_task_woken = pdFALSE;
  xQueueSendFromISR(static_cast<QueueHandle_t>(user_data), event_data, &higher_priority_task_woken);
  return higher_priority_task_woken == pdTRUE;
}

bool RmtOokReceiver::arm_(std::string& error) {
  rmt_receive_config_t config{};
  config.signal_range_min_ns = GLITCH_FILTER_NS;
  config.signal_range_max_ns = IDLE_END_NS;
  const esp_err_t result = rmt_receive(this->channel_, this->symbols_.data(), sizeof(this->symbols_), &config);
  if (result != ESP_OK) {
    append_error_(error, "rmt_receive", result);
    return false;
  }
  error.clear();
  return true;
}

bool RmtOokReceiver::convert_symbols_(const rmt_symbol_word_t* symbols, size_t count, RmtOokCapture& capture,
                                       std::string& error) const {
  capture = RmtOokCapture{};
  if (symbols == nullptr || count == 0U || count > SYMBOL_CAPACITY) {
    error = "rmt_invalid_symbol_count";
    return false;
  }
  RmtOokCapture current{};
  auto complete_segment = [&capture, &current]() {
    if (current.pcm_bit_count > capture.pcm_bit_count) {
      capture = current;
    }
    current = RmtOokCapture{};
  };
  auto append_or_split = [this, &complete_segment, &current, &error](bool high, uint32_t duration_us) {
    if (duration_us == 0U) {
      return;
    }
    const uint32_t units = (duration_us + (PCM_UNIT_US / 2U)) / PCM_UNIT_US;
    if (units == 0U || units > MAX_SIGNAL_RUN_UNITS || current.pcm_bit_count + units > RmtOokCapture::MAX_PCM_BITS) {
      complete_segment();
      return;
    }
    bool valid = true;
    this->append_run_(current, high, duration_us, valid, error);
    if (!valid) {
      complete_segment();
      error.clear();
    }
  };
  for (size_t index = 0; index < count; index++) {
    const rmt_symbol_word_t& symbol = symbols[index];
    append_or_split(symbol.level0 != 0U, symbol.duration0);
    append_or_split(symbol.level1 != 0U, symbol.duration1);
  }
  complete_segment();
  if (capture.pcm_bit_count < MIN_FRAME_PCM_BITS) {
    error = capture.pcm_bit_count == 0U ? "rmt_pcm_empty" : "rmt_pcm_short_segment";
    return false;
  }
  capture.symbol_count = static_cast<uint16_t>(count);
  return true;
}

void RmtOokReceiver::append_run_(RmtOokCapture& capture, bool high, uint32_t duration_us, bool& valid,
                                 std::string& error) {
  if (duration_us == 0U) {
    return;
  }
  const uint32_t units = (duration_us + (PCM_UNIT_US / 2U)) / PCM_UNIT_US;
  if (units == 0U || units > MAX_SIGNAL_RUN_UNITS || capture.pcm_bit_count + units > RmtOokCapture::MAX_PCM_BITS) {
    valid = false;
    error = high ? "rmt_pcm_high_run_invalid" : "rmt_pcm_low_run_invalid";
    return;
  }
  if (capture.transition_count == 0U) {
    if (high) {
      capture.first_high_us = duration_us;
    } else {
      capture.first_low_us = duration_us;
    }
  }
  for (uint32_t bit = 0; bit < units; bit++) {
    const uint16_t bit_index = capture.pcm_bit_count++;
    if (high) {
      capture.pcm_bytes[bit_index / 8U] |= static_cast<uint8_t>(1U << (7U - (bit_index % 8U)));
    }
  }
  capture.transition_count++;
}

}  // namespace proflame2_tembed
}  // namespace esphome
