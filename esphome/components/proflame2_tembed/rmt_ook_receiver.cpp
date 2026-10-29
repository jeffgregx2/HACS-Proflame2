#include "rmt_ook_receiver.h"

#include <cstdio>

#include "esp_err.h"
#ifdef USE_PROFLAME2_WAVEFORM_DIAGNOSTICS
#include <inttypes.h>
#include <new>
#include "esp_timer.h"
#include "esp_heap_caps.h"
#include "esp_random.h"
#include "esphome/core/log.h"
#endif

namespace esphome {
namespace proflame2_tembed {

#ifndef USE_PROFLAME2_BUFFERED_WAVEFORM_DIAGNOSTICS
namespace {

constexpr uint32_t PCM_UNIT_US = 417U;
constexpr uint32_t MAX_SIGNAL_RUN_UNITS = 3U;
constexpr uint16_t MIN_FRAME_PCM_BITS = 64U;

[[maybe_unused]] void append_error_(std::string& error, const char* prefix, esp_err_t result) {
  char buffer[64];
  snprintf(buffer, sizeof(buffer), "%s:%s", prefix, esp_err_to_name(result));
  error = buffer;
}

} // namespace
#endif

#ifndef USE_PROFLAME2_BUFFERED_WAVEFORM_DIAGNOSTICS
bool RmtOokReceiver::begin(int gpio_num, std::string& error) {
  this->end();
#ifdef USE_PROFLAME2_WAVEFORM_DIAGNOSTICS
  if (this->channel_ != nullptr || this->diagnostic_context_ != nullptr) {
    error = "diagnostic_shutdown_incomplete";
    return false;
  }
  this->diagnostic_session_ = esp_random();
  this->diagnostic_memory_open_ = true;
  this->diagnostic_memory_end_logged_ = false;
  this->log_diagnostic_memory_("before_allocate");
  this->diagnostic_symbols_ = static_cast<rmt_symbol_word_t*>(
      heap_caps_aligned_calloc(32, SYMBOL_CAPACITY, sizeof(rmt_symbol_word_t), MALLOC_CAP_INTERNAL | MALLOC_CAP_DMA));
  void* context_memory = heap_caps_calloc(1, sizeof(DiagnosticContext), MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
  if (this->diagnostic_symbols_ == nullptr || context_memory == nullptr) {
    heap_caps_free(context_memory);
    this->end();
    error = "diagnostic_dma_buffer_allocation_failed";
    return false;
  }
  this->diagnostic_context_ = new (context_memory) DiagnosticContext();
  this->diagnostic_started_us_ = esp_timer_get_time();
  this->diagnostic_exports_ = 0;
  this->diagnostic_rejected_ = 0;
  this->diagnostic_suppressed_ = 0;
  this->diagnostic_last_seal_us_ = 0;
#endif
  rmt_rx_channel_config_t config{};
  config.gpio_num = static_cast<gpio_num_t>(gpio_num);
  config.clk_src = RMT_CLK_SRC_DEFAULT;
  config.resolution_hz = RESOLUTION_HZ;
  config.mem_block_symbols = 64;
  config.intr_priority = 0;
  config.flags.invert_in = false;
  config.flags.with_dma = false;
#ifdef USE_PROFLAME2_WAVEFORM_DIAGNOSTICS
  config.flags.with_dma = true;
  config.mem_block_symbols = SYMBOL_CAPACITY;
#endif
  config.flags.allow_pd = false;
  config.flags.io_loop_back = false;

  esp_err_t result = rmt_new_rx_channel(&config, &this->channel_);
  if (result != ESP_OK) {
    this->channel_ = nullptr;
    append_error_(error, "rmt_new_rx_channel", result);
    this->end();
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
  void* callback_context = this->queue_;
#ifdef USE_PROFLAME2_WAVEFORM_DIAGNOSTICS
  callbacks.on_recv_done = &RmtOokReceiver::on_diagnostic_receive_done_;
  this->diagnostic_context_->queue = this->queue_;
  callback_context = this->diagnostic_context_;
#endif
  result = rmt_rx_register_event_callbacks(this->channel_, &callbacks, callback_context);
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
#ifdef USE_PROFLAME2_WAVEFORM_DIAGNOSTICS
  this->diagnostic_channel_enabled_ = true;
#endif
#ifdef USE_PROFLAME2_GPIO_EDGE_DIAGNOSTICS
  if (this->observer_pin_ == nullptr || this->observer_pin_->get_pin() != gpio_num ||
      !this->observer_.begin(this->observer_pin_, error)) {
    if (error.empty())
      error = "gpio_observer_pin_mismatch";
    this->end();
    return false;
  }
#endif
#ifdef USE_PROFLAME2_WAVEFORM_DIAGNOSTICS
  // Log before arming so allocator inspection does not interrupt acquisition.
  this->log_diagnostic_memory_("allocated");
#endif
  if (!this->arm_(error)) {
    this->end();
    return false;
  }
#ifdef USE_PROFLAME2_WAVEFORM_DIAGNOSTICS
  this->diagnostic_session_open_ = true;
#ifdef USE_PROFLAME2_GPIO_EDGE_DIAGNOSTICS
  ESP_LOGI("proflame2.raw", "PFGPIO1 session=%" PRIu32 " capacity=2048 irq_budget=32 irq_window_us=1000 cache_off_safe=0",
           this->diagnostic_session_);
#endif
  ESP_LOGI("proflame2.raw", "PFSESSION session=%" PRIu32 " state=armed duration_ms=%" PRIu32, this->diagnostic_session_,
           this->diagnostic_limits_.session_ms);
#endif
  return true;
}

void RmtOokReceiver::end() {
#ifdef USE_PROFLAME2_WAVEFORM_DIAGNOSTICS
  if (this->diagnostic_session_open_ && !this->diagnostic_memory_end_logged_) {
    this->log_diagnostic_memory_("capture_end");
    this->diagnostic_memory_end_logged_ = true;
  }
#endif
#ifdef USE_PROFLAME2_GPIO_EDGE_DIAGNOSTICS
  std::string observer_error;
  if (!this->observer_.end(observer_error)) {
    this->diagnostic_state_ = RawCaptureState::FAILED;
    ESP_LOGE("proflame2.raw", "PFSESSION session=%" PRIu32 " state=failed error=%s",
             this->diagnostic_session_, observer_error.c_str());
    return;
  }
#endif
#ifdef USE_PROFLAME2_WAVEFORM_DIAGNOSTICS
  if (this->diagnostic_context_ != nullptr) {
    portENTER_CRITICAL(&this->diagnostic_context_->mux);
    this->diagnostic_context_->sealed = true;
    portEXIT_CRITICAL(&this->diagnostic_context_->mux);
  }
#endif
  if (this->channel_ != nullptr) {
#ifdef USE_PROFLAME2_WAVEFORM_DIAGNOSTICS
    if (this->diagnostic_channel_enabled_ && rmt_disable(this->channel_) != ESP_OK) {
      this->diagnostic_state_ = RawCaptureState::FAILED;
      ESP_LOGE("proflame2.raw", "PFSESSION session=%" PRIu32 " state=failed error=shutdown_disable_failed",
               this->diagnostic_session_);
      return;
    }
    this->diagnostic_channel_enabled_ = false;
    if (rmt_del_channel(this->channel_) != ESP_OK) {
      this->diagnostic_state_ = RawCaptureState::FAILED;
      ESP_LOGE("proflame2.raw", "PFSESSION session=%" PRIu32 " state=failed error=shutdown_delete_failed",
               this->diagnostic_session_);
      return;
    }
#else
    rmt_disable(this->channel_);
    rmt_del_channel(this->channel_);
#endif
    this->channel_ = nullptr;
  }
  if (this->queue_ != nullptr) {
    vQueueDelete(this->queue_);
    this->queue_ = nullptr;
  }
#ifdef USE_PROFLAME2_WAVEFORM_DIAGNOSTICS
  heap_caps_free(this->diagnostic_symbols_);
  this->diagnostic_symbols_ = nullptr;
  this->raw_pending_ = false;
  if (this->diagnostic_context_ != nullptr) {
    this->diagnostic_context_->~DiagnosticContext();
    heap_caps_free(this->diagnostic_context_);
    this->diagnostic_context_ = nullptr;
  }
  this->diagnostic_channel_enabled_ = false;
  if (this->diagnostic_memory_open_) {
    this->log_diagnostic_memory_("released");
    this->diagnostic_memory_open_ = false;
  }
  this->close_diagnostic_session_("aborted");
  this->diagnostic_state_ = RawCaptureState::PAUSED;
#endif
}

bool RmtOokReceiver::poll(RmtOokCapture& capture, std::string& error) {
  if (this->channel_ == nullptr || this->queue_ == nullptr) {
    error = "rmt_not_active";
    return false;
  }
#ifdef USE_PROFLAME2_WAVEFORM_DIAGNOSTICS
  return this->poll_diagnostic_(capture, error);
#else
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
#endif
}

#ifdef USE_PROFLAME2_WAVEFORM_DIAGNOSTICS
bool RmtOokReceiver::poll_diagnostic_(RmtOokCapture& capture, std::string& error) {
  if (this->diagnostic_state_ != RawCaptureState::ACQUIRING)
    return false;
  auto* context = this->diagnostic_context_;
  const int64_t now = esp_timer_get_time();
  const bool deadline =
      now - this->diagnostic_started_us_ >= static_cast<int64_t>(this->diagnostic_limits_.session_ms) * 1000;
  portENTER_CRITICAL(&context->mux);
  bool sealed = context->sealed;
  if (deadline && !sealed) {
    context->window.timed_out = true;
    context->sealed = true;
    sealed = true;
  }
  portEXIT_CRITICAL(&context->mux);
  if (!sealed)
    return false;
  const int64_t polled_us = esp_timer_get_time();
  const esp_err_t stopped = rmt_disable(this->channel_);
  if (stopped != ESP_OK) {
    this->diagnostic_state_ = RawCaptureState::FAILED;
    append_error_(error, "diagnostic_stop_failed", stopped);
    ESP_LOGE("proflame2.raw", "PFSESSION session=%" PRIu32 " state=failed code=%d", this->diagnostic_session_, stopped);
    return false;
  }
  this->diagnostic_channel_enabled_ = false;
  auto& window = context->window;
  portENTER_CRITICAL(&context->mux);
  window.ignored_callbacks = context->ignored_callbacks;
  window.ignored_symbols = context->ignored_symbols;
  portEXIT_CRITICAL(&context->mux);
  window.poll_us = polled_us;
  window.seal_us = esp_timer_get_time();
  this->diagnostic_last_seal_us_ = window.seal_us;
  this->diagnostic_state_ = RawCaptureState::SEALED;
  // ISR writes only until sealed; no DMA buffer is reused during conversion/export.
  for (size_t index = 0; index < window.count; index++) {
    const uint32_t word = window.words[index];
    this->diagnostic_symbols_[index].duration0 = word & 0x7FFF;
    this->diagnostic_symbols_[index].level0 = (word >> 15) & 1;
    this->diagnostic_symbols_[index].duration1 = (word >> 16) & 0x7FFF;
    this->diagnostic_symbols_[index].level1 = (word >> 31) & 1;
  }
  const bool converted = this->convert_symbols_(this->diagnostic_symbols_, window.count, capture, error);
  const bool plausible = window.has_frame_prefix();
  this->raw_pending_ = plausible || (window.count > 0 && this->diagnostic_limits_.capture_rejected &&
                                     this->diagnostic_rejected_ < this->diagnostic_limits_.max_rejected);
  if (!plausible && this->raw_pending_)
    this->diagnostic_rejected_++;
  if (!this->raw_pending_)
    this->diagnostic_suppressed_++;
#ifdef USE_PROFLAME2_GPIO_EDGE_DIAGNOSTICS
  if (this->raw_pending_) {
    this->observer_.snapshot(window.arm_us);
    this->observer_export_done_ = false;
  }
#endif
  return converted;
}

bool IRAM_ATTR RmtOokReceiver::on_diagnostic_receive_done_(rmt_channel_handle_t, const rmt_rx_done_event_data_t* event,
                                                           void* user_data) {
  auto* context = static_cast<DiagnosticContext*>(user_data);
  BaseType_t woken = pdFALSE;
  portENTER_CRITICAL_ISR(&context->mux);
  if (!context->sealed) {
    auto& window = context->window;
    window.callback_count++;
    window.callback_us = esp_timer_get_time();
    if (event->received_symbols == nullptr || event->num_symbols > RawOokWindow::CAPACITY) {
      window.saturated = true;
    } else {
      for (size_t index = 0; index < event->num_symbols; index++) {
        if (window.count == RawOokWindow::CAPACITY) {
          window.saturated = true;
          break;
        }
        const auto& symbol = event->received_symbols[index];
        window.words[window.count++] = symbol.duration0 | (static_cast<uint32_t>(symbol.level0) << 15U) |
                                       (static_cast<uint32_t>(symbol.duration1) << 16U) |
                                       (static_cast<uint32_t>(symbol.level1) << 31U);
      }
    }
    window.final_seen = event->flags.is_last;
    if (window.count == RawOokWindow::CAPACITY)
      window.saturated = true;
    if (window.final_seen || window.saturated) {
      context->sealed = true;
      if (xQueueSendFromISR(context->queue, event, &woken) != pdTRUE)
        window.queue_losses++;
    }
  } else {
    context->ignored_callbacks++;
    if (event->num_symbols <= RawOokWindow::CAPACITY)
      context->ignored_symbols += event->num_symbols;
  }
  portEXIT_CRITICAL_ISR(&context->mux);
  return woken == pdTRUE;
}

void RmtOokReceiver::export_raw_window() {
  if (this->diagnostic_state_ == RawCaptureState::SEALED) {
    this->diagnostic_state_ = RawCaptureState::EXPORTING;
    this->diagnostic_export_phase_ = 0;
    this->diagnostic_export_offset_ = 0;
    this->diagnostic_history_segment_ = 0;
    this->diagnostic_history_offset_ = 0;
  }
  if (this->diagnostic_state_ != RawCaptureState::EXPORTING)
    return;
  const auto& window = this->diagnostic_context_->window;
  auto& history = this->diagnostic_context_->history;
  if (this->raw_pending_) {
#ifdef USE_PROFLAME2_GPIO_EDGE_DIAGNOSTICS
    if (!this->observer_export_done_) {
      this->observer_export_done_ = this->observer_.export_snapshot(this->diagnostic_session_, window.sequence);
      return;
    }
#endif
    const bool historical = this->diagnostic_history_segment_ < history.segment_count;
    const RawOokMetadata& record =
        historical ? static_cast<const RawOokMetadata&>(history.segments[this->diagnostic_history_segment_])
                   : static_cast<const RawOokMetadata&>(window);
    const uint32_t* words = historical ? history.words.data() + this->diagnostic_history_offset_ : window.words.data();
    const char* prefix = historical ? "PFHIST1" : "PFRAW2";
    // One record per loop pass keeps large exports from monopolizing the main task.
    if (this->diagnostic_export_phase_ == 0) {
      if (historical) {
        ESP_LOGI("proflame2.raw", "PFHIST1 parent=%" PRIu32 " source_offset=%u", window.sequence,
                 static_cast<unsigned>(history.segments[this->diagnostic_history_segment_].source_offset));
        this->diagnostic_export_phase_ = 1;
      } else {
        ESP_LOGI("proflame2.raw",
                 "PFPRE1 session=%" PRIu32 " id=%" PRIu32 " requested_us=75000 duration_us=%" PRIu32
                 " segments=%u short=%u limited=%u",
                 this->diagnostic_session_, window.sequence, history.duration_us,
                 static_cast<unsigned>(history.segment_count),
                 static_cast<unsigned>(history.duration_us < RawOokHistory::TARGET_US),
                 static_cast<unsigned>(history.limited));
        this->diagnostic_export_phase_ = 1;
      }
      return;
    }
    if (this->diagnostic_export_phase_ == 1) {
      ESP_LOGI("proflame2.raw",
               "%s session=%" PRIu32 " id=%" PRIu32 " count=%u saturated=%u poll_us=%" PRId64 " rearm_us=%" PRId64
               " resolution_hz=1000000 filter_ns=%" PRIu32 " idle_ns=30000000 checksum=%08" PRIx32,
               prefix, this->diagnostic_session_, record.sequence, static_cast<unsigned>(record.count),
               static_cast<unsigned>(record.saturated), record.poll_us, record.arm_us,
               this->diagnostic_limits_.filter_ns, RawOokWindow::checksum(words, record.count));
      this->diagnostic_export_phase_ = 2;
      return;
    }
    if (this->diagnostic_export_phase_ == 2) {
      ESP_LOGI("proflame2.raw",
               "%s id=%" PRIu32 " arm_us=%" PRId64 " callback_us=%" PRId64 " seal_us=%" PRId64 " gap_us=%" PRId64
               " callbacks=%" PRIu32 " queue_losses=%" PRIu32 " ignored_callbacks=%" PRIu32 " ignored_symbols=%" PRIu32
               " final=%u timeout=%u",
               prefix, record.sequence, record.arm_us, record.callback_us, record.seal_us, record.prior_gap_us,
               record.callback_count, record.queue_losses, record.ignored_callbacks, record.ignored_symbols,
               static_cast<unsigned>(record.final_seen), static_cast<unsigned>(record.timed_out));
      this->diagnostic_export_phase_ = 3;
      return;
    }
    char chunk[16 * 8 + 1];
    if (this->diagnostic_export_offset_ < record.count) {
      const size_t offset = this->diagnostic_export_offset_;
      size_t length = 0;
      for (size_t index = offset; index < record.count && index < offset + 16; index++) {
        snprintf(chunk + length, sizeof(chunk) - length, "%08" PRIx32, words[index]);
        length += 8;
      }
      ESP_LOGI("proflame2.raw", "%s id=%" PRIu32 " offset=%u words=%s", prefix, record.sequence,
               static_cast<unsigned>(offset), chunk);
      this->diagnostic_export_offset_ += 16;
      return;
    }
    ESP_LOGI("proflame2.raw", "%s id=%" PRIu32 " end=1", prefix, record.sequence);
    if (historical) {
      this->diagnostic_history_offset_ += record.count;
      this->diagnostic_history_segment_++;
      this->diagnostic_export_phase_ = 0;
      this->diagnostic_export_offset_ = 0;
      return;
    }
    this->raw_pending_ = false;
    this->diagnostic_exports_++;
  }
  // Preserve even rejected/noise windows for the next selected capture. Do not
  // prepend them to the main window: rearm/export gaps are not measured RF.
  history.append(window);
  const bool expired = esp_timer_get_time() - this->diagnostic_started_us_ >=
                       static_cast<int64_t>(this->diagnostic_limits_.session_ms) * 1000;
  if (expired || this->diagnostic_exports_ >= this->diagnostic_limits_.max_exports) {
    this->diagnostic_state_ = RawCaptureState::PAUSED;
    this->close_diagnostic_session_("paused");
    return;
  }
  std::string error;
  if (!this->arm_(error)) {
    this->diagnostic_state_ = RawCaptureState::FAILED;
    ESP_LOGE("proflame2.raw", "PFSESSION session=%" PRIu32 " state=failed error=%s", this->diagnostic_session_,
             error.c_str());
  }
}

void RmtOokReceiver::close_diagnostic_session_(const char* state) {
  if (!this->diagnostic_session_open_)
    return;
  if (!this->diagnostic_memory_end_logged_) {
    this->log_diagnostic_memory_("capture_end");
    this->diagnostic_memory_end_logged_ = true;
  }
#ifdef USE_PROFLAME2_GPIO_EDGE_DIAGNOSTICS
  std::string error;
  if (!this->observer_.end(error)) {
    this->diagnostic_state_ = RawCaptureState::FAILED;
    ESP_LOGE("proflame2.raw", "PFSESSION session=%" PRIu32 " state=failed error=%s", this->diagnostic_session_, error.c_str());
    return;
  }
#endif
  ESP_LOGI("proflame2.raw",
           "PFSESSION session=%" PRIu32 " state=%s exports=%" PRIu32 " suppressed=%" PRIu32 " end_us=%" PRId64,
           this->diagnostic_session_, state, this->diagnostic_exports_, this->diagnostic_suppressed_,
           esp_timer_get_time());
  this->diagnostic_session_open_ = false;
}

void RmtOokReceiver::log_diagnostic_memory_(const char* phase) const {
  multi_heap_info_t internal{}, dma{}, psram{};
  heap_caps_get_info(&internal, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
  heap_caps_get_info(&dma, MALLOC_CAP_INTERNAL | MALLOC_CAP_DMA | MALLOC_CAP_8BIT);
  heap_caps_get_info(&psram, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
  // Capability pools overlap; minima are allocator low-water marks since boot.
  ESP_LOGI("proflame2.memory",
           "PFMEM1 session=%" PRIu32 " phase=%s internal_free=%u internal_largest=%u internal_min=%u"
           " dma_free=%u dma_largest=%u dma_min=%u psram_free=%u psram_largest=%u psram_min=%u",
           this->diagnostic_session_, phase,
           static_cast<unsigned>(internal.total_free_bytes), static_cast<unsigned>(internal.largest_free_block),
           static_cast<unsigned>(internal.minimum_free_bytes), static_cast<unsigned>(dma.total_free_bytes),
           static_cast<unsigned>(dma.largest_free_block), static_cast<unsigned>(dma.minimum_free_bytes),
           static_cast<unsigned>(psram.total_free_bytes), static_cast<unsigned>(psram.largest_free_block),
           static_cast<unsigned>(psram.minimum_free_bytes));
}
#endif

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
#ifdef USE_PROFLAME2_WAVEFORM_DIAGNOSTICS
  if (!this->diagnostic_channel_enabled_) {
    const esp_err_t enabled = rmt_enable(this->channel_);
    if (enabled != ESP_OK) {
      append_error_(error, "diagnostic_enable_failed", enabled);
      return false;
    }
    this->diagnostic_channel_enabled_ = true;
  }
  rmt_rx_done_event_data_t stale{};
  while (xQueueReceive(this->queue_, &stale, 0) == pdTRUE) {
  }
  auto* context = this->diagnostic_context_;
  portENTER_CRITICAL(&context->mux);
  context->window.count = 0;
  context->window.callback_count = 0;
  context->window.queue_losses = 0;
  context->window.saturated = false;
  context->window.timed_out = false;
  context->window.final_seen = false;
  context->window.callback_us = 0;
  context->ignored_callbacks = 0;
  context->ignored_symbols = 0;
  context->window.sequence = ++this->raw_sequence_;
  context->window.arm_us = esp_timer_get_time();
  context->window.prior_gap_us =
      this->diagnostic_last_seal_us_ == 0 ? 0 : context->window.arm_us - this->diagnostic_last_seal_us_;
  context->sealed = false;
  portEXIT_CRITICAL(&context->mux);
  config.signal_range_min_ns = this->diagnostic_limits_.filter_ns;
  config.flags.en_partial_rx = true;
  this->diagnostic_state_ = RawCaptureState::ACQUIRING;
#endif
  const esp_err_t result =
      rmt_receive(this->channel_, this->symbol_buffer_(), SYMBOL_CAPACITY * sizeof(rmt_symbol_word_t), &config);
  if (result != ESP_OK) {
    append_error_(error, "rmt_receive", result);
    return false;
  }
  error.clear();
  return true;
}

#else
bool RmtOokReceiver::begin(int gpio_num, std::string& error) {
  return this->buffered_.begin(gpio_num, error);
}
void RmtOokReceiver::end() { this->buffered_.request_stop(); }
bool RmtOokReceiver::poll(RmtOokCapture&, std::string& error) {
  error.clear();
  this->buffered_.process();
  return false;
}
void RmtOokReceiver::export_raw_window() { this->buffered_.export_tick(); }
#endif

#ifndef USE_PROFLAME2_BUFFERED_WAVEFORM_DIAGNOSTICS
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
#endif

} // namespace proflame2_tembed
} // namespace esphome
