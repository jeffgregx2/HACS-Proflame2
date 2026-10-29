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
#include "esphome/core/defines.h"
#ifdef USE_PROFLAME2_BUFFERED_WAVEFORM_DIAGNOSTICS
#include "buffered_ook_capture.h"
#endif
#ifdef USE_PROFLAME2_GPIO_EDGE_DIAGNOSTICS
#include "gpio_edge_observer.h"
#endif

#ifdef USE_PROFLAME2_WAVEFORM_DIAGNOSTICS
#include "esp_attr.h"
#include "raw_ook_window.h"
#endif

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
  bool active() const {
#ifdef USE_PROFLAME2_BUFFERED_WAVEFORM_DIAGNOSTICS
    return this->buffered_.active();
#else
    return this->channel_ != nullptr;
#endif
  }
#ifdef USE_PROFLAME2_GPIO_EDGE_DIAGNOSTICS
  void set_gpio_edge_observer_pin(InternalGPIOPin* pin) {
#ifdef USE_PROFLAME2_BUFFERED_WAVEFORM_DIAGNOSTICS
    this->buffered_.set_pin(pin);
#else
    this->observer_pin_ = pin;
#endif
  }
#endif
#ifdef USE_PROFLAME2_WAVEFORM_DIAGNOSTICS
  void export_raw_window();
  void set_diagnostic_limits(const RawCaptureLimits& limits) {
#ifdef USE_PROFLAME2_BUFFERED_WAVEFORM_DIAGNOSTICS
    this->buffered_.set_limits(limits);
#else
    this->diagnostic_limits_ = limits;
#endif
  }
#endif

private:
#ifdef USE_PROFLAME2_BUFFERED_WAVEFORM_DIAGNOSTICS
  BufferedOokCapture buffered_{};
#else
#ifdef USE_PROFLAME2_GPIO_EDGE_DIAGNOSTICS
  GPIOEdgeObserver observer_{};
  InternalGPIOPin* observer_pin_{nullptr};
  bool observer_export_done_{true};
#endif
#ifdef USE_PROFLAME2_WAVEFORM_DIAGNOSTICS
  static constexpr size_t SYMBOL_CAPACITY = RawOokWindow::CAPACITY;
  struct DiagnosticContext {
    portMUX_TYPE mux = portMUX_INITIALIZER_UNLOCKED;
    QueueHandle_t queue{nullptr};
    RawOokWindow window{};
    RawOokHistory history{};
    bool sealed{false};
    uint32_t ignored_callbacks{0};
    uint32_t ignored_symbols{0};
  };
  DiagnosticContext* diagnostic_context_{nullptr};
  RawCaptureLimits diagnostic_limits_{};
  RawCaptureState diagnostic_state_{RawCaptureState::PAUSED};
  int64_t diagnostic_started_us_{0};
  int64_t diagnostic_last_seal_us_{0};
  uint32_t diagnostic_session_{0};
  uint32_t diagnostic_exports_{0};
  uint32_t diagnostic_rejected_{0};
  uint32_t diagnostic_suppressed_{0};
  size_t diagnostic_export_offset_{0};
  size_t diagnostic_history_segment_{0};
  size_t diagnostic_history_offset_{0};
  uint8_t diagnostic_export_phase_{0};
  bool diagnostic_session_open_{false};
  bool diagnostic_memory_open_{false};
  bool diagnostic_memory_end_logged_{false};
  uint32_t raw_sequence_{0};
  bool raw_pending_{false};
  bool diagnostic_channel_enabled_{false};
  bool poll_diagnostic_(RmtOokCapture& capture, std::string& error);
  void close_diagnostic_session_(const char* state);
  void log_diagnostic_memory_(const char* phase) const;
  static bool IRAM_ATTR on_diagnostic_receive_done_(rmt_channel_handle_t channel,
                                                    const rmt_rx_done_event_data_t* event_data, void* user_data);
#else
  static constexpr size_t SYMBOL_CAPACITY = 256;
#endif

  static bool on_receive_done_(rmt_channel_handle_t channel, const rmt_rx_done_event_data_t* event_data,
                               void* user_data);
  bool arm_(std::string& error);
  rmt_symbol_word_t* symbol_buffer_() {
#ifdef USE_PROFLAME2_WAVEFORM_DIAGNOSTICS
    return this->diagnostic_symbols_;
#else
    return this->symbols_.data();
#endif
  }
#endif
#ifndef USE_PROFLAME2_BUFFERED_WAVEFORM_DIAGNOSTICS
  bool convert_symbols_(const rmt_symbol_word_t* symbols, size_t count, RmtOokCapture& capture,
                        std::string& error) const;
  static void append_run_(RmtOokCapture& capture, bool high, uint32_t duration_us, bool& valid, std::string& error);
#endif

#ifndef USE_PROFLAME2_BUFFERED_WAVEFORM_DIAGNOSTICS
  rmt_channel_handle_t channel_{nullptr};
  QueueHandle_t queue_{nullptr};
#ifdef USE_PROFLAME2_WAVEFORM_DIAGNOSTICS
  rmt_symbol_word_t* diagnostic_symbols_{nullptr};
#else
  std::array<rmt_symbol_word_t, SYMBOL_CAPACITY> symbols_{};
#endif
#endif
};

} // namespace proflame2_tembed
} // namespace esphome
