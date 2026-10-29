#pragma once
#include "esphome/core/defines.h"

#ifdef USE_PROFLAME2_BUFFERED_WAVEFORM_DIAGNOSTICS
#ifndef USE_PROFLAME2_WAVEFORM_DIAGNOSTICS
#error "Buffered capture requires waveform diagnostics"
#endif

#include <string>
#include "driver/rmt_rx.h"
#include "esp_attr.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "raw_ook_window.h"
#ifdef USE_PROFLAME2_GPIO_EDGE_DIAGNOSTICS
#include "gpio_edge_observer.h"
#endif

namespace esphome {
namespace proflame2_tembed {

// Acquisition owns DMA/internal slots. Main-loop analysis owns PSRAM records.
// Only sealed slot indexes cross the task boundary; no borrowed DMA pointers.
class BufferedOokCapture {
public:
  bool begin(int gpio_num, std::string& error);
  void request_stop();
  void process();
  void export_tick();
  bool active() const {
    return this->context_ != nullptr;
  }
  void set_limits(const RawCaptureLimits& limits) {
    if (!this->active())
      this->limits_ = limits;
  }
#ifdef USE_PROFLAME2_GPIO_EDGE_DIAGNOSTICS
  void set_pin(InternalGPIOPin* pin) {
    this->pin_ = pin;
  }
#endif

private:
  static constexpr size_t SLOT_COUNT = 2;
  static constexpr uint32_t TASK_STACK_BYTES = 6144;
  enum class Ownership : uint8_t { FREE, FILLING, SEALED, READY, PROCESSING };
  struct Slot {
    RawOokWindow raw{};
    int64_t arm_after_us{0};
    Ownership owner{Ownership::FREE};
  };
  struct Retained {
    RawOokWindow raw{};
    RawOokHistory history{};
    int64_t arm_after_us{0};
    int64_t gpio_anchor_us{0};
    bool gpio_anchor_observed{false};
#ifdef USE_PROFLAME2_GPIO_EDGE_DIAGNOSTICS
    GPIOEdgeObserver::Snapshot gpio{};
#endif
  };
  struct Context {
    portMUX_TYPE mux = portMUX_INITIALIZER_UNLOCKED;
    Slot slots[SLOT_COUNT]{};
    TaskHandle_t task{nullptr};
    QueueHandle_t ready{nullptr};
    SemaphoreHandle_t started{nullptr};
    SemaphoreHandle_t finished{nullptr};
    uint8_t filling{0};
    uint32_t sequence{0};
    uint32_t pool_losses{0};
    uint32_t queue_losses{0};
    uint32_t callback_losses{0};
    uint32_t retention_losses{0};
    int driver_error{0};
    bool stop{false};
    bool limit{false};
    bool enabled{false};
    int64_t ended_us{0};
    int64_t last_seal_us{0};
    uint32_t stack_free_bytes{0};
  };
  Context* context_{nullptr};
  rmt_channel_handle_t channel_{nullptr};
  rmt_symbol_word_t* dma_{nullptr};
  Retained* records_{nullptr};
  RawOokHistory* history_{nullptr};
  RawCaptureLimits limits_{};
  uint32_t session_{0};
  int64_t started_us_{0};
  size_t retained_{0};
  size_t exported_{0};
  uint32_t suppressed_{0};
  uint16_t rejected_{0};
  bool opened_{false};
  bool stopped_{false};
  bool hardware_released_{false};
  bool memory_open_{false};
  bool end_memory_logged_{false};
  uint8_t export_phase_{0};
  size_t export_segment_{0};
  size_t export_offset_{0};
  size_t history_offset_{0};
  bool gpio_export_done_{false};
#ifdef USE_PROFLAME2_GPIO_EDGE_DIAGNOSTICS
  InternalGPIOPin* pin_{nullptr};
  GPIOEdgeObserver observer_{};
#endif
  static bool IRAM_ATTR on_receive_(rmt_channel_handle_t, const rmt_rx_done_event_data_t*, void*);
  static void task_entry_(void* arg);
  void acquire_();
  bool arm_(uint8_t index);
  bool stop_driver_();
  bool release_hardware_();
  void release_();
  void memory_(const char* phase) const;
  void export_record_();
};

} // namespace proflame2_tembed
} // namespace esphome
#endif
