// Bounded ESP-IDF RMT receiver for the CC1101 asynchronous serial output.
//
// This helper intentionally has no Proflame-specific parsing. It preserves
// the demodulated high/low timing stream as PCM bits for HA-side validation.

#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <string>

#include "driver/rmt_rx.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"

namespace esphome {
namespace proflame2_tembed {

struct RmtOokCapture {
  static constexpr size_t MAX_PCM_BITS = 1024;

  std::array<uint8_t, MAX_PCM_BITS / 8> pcm_bytes{};
  uint16_t pcm_bit_count{0};
  uint16_t symbol_count{0};
  uint16_t transition_count{0};
  uint32_t first_high_us{0};
  uint32_t first_low_us{0};
};

class RmtOokReceiver {
 public:
  static constexpr uint32_t RESOLUTION_HZ = 1000000U;
  static constexpr uint32_t GLITCH_FILTER_NS = 3000U;
  static constexpr uint32_t IDLE_END_NS = 30000000U;

  bool begin(int gpio_num, std::string& error);
  void end();
  bool poll(RmtOokCapture& capture, std::string& error);
  bool active() const { return this->channel_ != nullptr; }

 private:
  static constexpr size_t SYMBOL_CAPACITY = 256;

  static bool on_receive_done_(rmt_channel_handle_t channel, const rmt_rx_done_event_data_t* event_data,
                               void* user_data);
  bool arm_(std::string& error);
  bool convert_symbols_(const rmt_symbol_word_t* symbols, size_t count, RmtOokCapture& capture,
                        std::string& error) const;
  static void append_run_(RmtOokCapture& capture, bool high, uint32_t duration_us, bool& valid,
                          std::string& error);

  rmt_channel_handle_t channel_{nullptr};
  QueueHandle_t queue_{nullptr};
  std::array<rmt_symbol_word_t, SYMBOL_CAPACITY> symbols_{};
};

}  // namespace proflame2_tembed
}  // namespace esphome
