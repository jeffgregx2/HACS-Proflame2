#include "buffered_ook_capture.h"

#ifdef USE_PROFLAME2_BUFFERED_WAVEFORM_DIAGNOSTICS
#include <cstdio>
#include <inttypes.h>
#include <new>
#include "esp_heap_caps.h"
#include "esp_random.h"
#include "esp_timer.h"
#include "esphome/core/log.h"

namespace esphome {
namespace proflame2_tembed {

bool BufferedOokCapture::begin(int gpio_num, std::string& error) {
  if (this->active()) {
    error = "buffered_capture_busy_or_exporting";
    return false;
  }
  if (this->limits_.max_exports == 0 || this->limits_.max_exports > 64) {
    error = "buffered_capture_limit_invalid";
    return false;
  }
  this->session_ = esp_random();
  this->retained_ = this->exported_ = this->suppressed_ = this->rejected_ = 0;
  this->opened_ = this->stopped_ = this->hardware_released_ = false;
  this->end_memory_logged_ = false;
  this->export_phase_ = this->export_segment_ = this->export_offset_ = this->history_offset_ = 0;
  this->gpio_export_done_ = false;
#ifdef USE_PROFLAME2_GPIO_EDGE_DIAGNOSTICS
  this->observer_.reset_export();
#endif
  this->memory_open_ = true;
  this->memory_("before_allocate");
  void* memory = heap_caps_calloc(1, sizeof(Context), MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
  if (memory != nullptr)
    this->context_ = new (memory) Context();
  this->dma_ = static_cast<rmt_symbol_word_t*>(heap_caps_aligned_calloc(
      32, RawOokWindow::CAPACITY, sizeof(rmt_symbol_word_t), MALLOC_CAP_INTERNAL | MALLOC_CAP_DMA));
  this->records_ = static_cast<Retained*>(
      heap_caps_calloc(this->limits_.max_exports, sizeof(Retained), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
  memory = heap_caps_calloc(1, sizeof(RawOokHistory), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
  if (memory != nullptr)
    this->history_ = new (memory) RawOokHistory();
  if (this->context_ == nullptr || this->dma_ == nullptr || this->records_ == nullptr || this->history_ == nullptr) {
    error = "buffered_capture_internal_or_psram_allocation_failed";
    this->release_();
    return false;
  }
  for (size_t index = 0; index < this->limits_.max_exports; index++)
    new (&this->records_[index]) Retained();
  auto* ctx = this->context_;
  ctx->ready = xQueueCreate(SLOT_COUNT, sizeof(uint8_t));
  ctx->started = xSemaphoreCreateBinary();
  ctx->finished = xSemaphoreCreateBinary();
  if (ctx->ready == nullptr || ctx->started == nullptr || ctx->finished == nullptr) {
    error = "buffered_capture_queue_allocation_failed";
    this->release_();
    return false;
  }
  rmt_rx_channel_config_t config{};
  config.gpio_num = static_cast<gpio_num_t>(gpio_num);
  config.clk_src = RMT_CLK_SRC_DEFAULT;
  config.resolution_hz = 1000000;
  config.mem_block_symbols = RawOokWindow::CAPACITY;
  config.flags.with_dma = true;
  auto status = rmt_new_rx_channel(&config, &this->channel_);
  rmt_rx_event_callbacks_t callbacks{};
  callbacks.on_recv_done = &BufferedOokCapture::on_receive_;
  if (status == ESP_OK)
    status = rmt_rx_register_event_callbacks(this->channel_, &callbacks, ctx);
  if (status == ESP_OK) {
    status = rmt_enable(this->channel_);
    ctx->enabled = status == ESP_OK;
  }
  if (status != ESP_OK) {
    error = "buffered_capture_driver_setup_failed";
    this->release_();
    return false;
  }
#ifdef USE_PROFLAME2_GPIO_EDGE_DIAGNOSTICS
  if (this->pin_ == nullptr || this->pin_->get_pin() != gpio_num || !this->observer_.begin(this->pin_, error)) {
    if (error.empty())
      error = "buffered_capture_observer_pin_invalid";
    this->release_();
    return false;
  }
#endif
  this->started_us_ = esp_timer_get_time();
  if (xTaskCreate(&BufferedOokCapture::task_entry_, "pf_acquire", TASK_STACK_BYTES, this, 6, &ctx->task) != pdPASS) {
    error = "buffered_capture_task_allocation_failed";
    this->release_();
    return false;
  }
  this->memory_("allocated");
  ESP_LOGI("proflame2.memory",
           "PFSTORE1 session=%" PRIu32 " internal_buffers_bytes=%u task_stack_bytes=%u psram_records_bytes=%u",
           this->session_, static_cast<unsigned>(sizeof(Context) + RawOokWindow::CAPACITY * sizeof(rmt_symbol_word_t)),
           static_cast<unsigned>(TASK_STACK_BYTES),
           static_cast<unsigned>(sizeof(Retained) * this->limits_.max_exports + sizeof(RawOokHistory)));
  xTaskNotifyGive(ctx->task);
  if (xSemaphoreTake(ctx->started, pdMS_TO_TICKS(2000)) != pdTRUE) {
    error = "buffered_capture_task_start_timeout";
    this->request_stop();
    return false; // Keep ownership until the task actually acknowledges shutdown.
  }
  portENTER_CRITICAL(&ctx->mux);
  const bool startup_failed = ctx->driver_error != ESP_OK;
  portEXIT_CRITICAL(&ctx->mux);
  if (startup_failed) {
    error = "buffered_capture_initial_arm_failed";
    this->request_stop();
    if (xSemaphoreTake(ctx->finished, pdMS_TO_TICKS(2000)) == pdTRUE) {
      this->stopped_ = true;
      this->release_();
    }
    return false;
  }
  this->opened_ = true;
#ifdef USE_PROFLAME2_GPIO_EDGE_DIAGNOSTICS
  ESP_LOGI("proflame2.raw",
           "PFGPIO1 session=%" PRIu32 " capacity=2048 irq_budget=32 irq_window_us=1000 cache_off_safe=0",
           this->session_);
#endif
  ESP_LOGI("proflame2.raw", "PFSESSION session=%" PRIu32 " state=armed duration_ms=%" PRIu32, this->session_,
           this->limits_.session_ms);
  ESP_LOGI("proflame2.raw", "PFBATCH1 session=%" PRIu32 " slots=2 retained_limit=%u deferred_export=1", this->session_,
           static_cast<unsigned>(this->limits_.max_exports));
  return true;
}

bool BufferedOokCapture::arm_(uint8_t index) {
  auto* ctx = this->context_;
  auto& slot = ctx->slots[index];
  portENTER_CRITICAL(&ctx->mux);
  static_cast<RawOokMetadata&>(slot.raw) = RawOokMetadata{};
  slot.raw.sequence = ++ctx->sequence;
  slot.arm_after_us = 0;
  slot.owner = Ownership::FILLING;
  ctx->filling = index;
  portEXIT_CRITICAL(&ctx->mux);
  rmt_receive_config_t config{};
  config.signal_range_min_ns = this->limits_.filter_ns;
  config.signal_range_max_ns = 30000000;
  config.flags.en_partial_rx = true;
  auto status = ESP_OK;
  if (!ctx->enabled) {
    status = rmt_enable(this->channel_);
    ctx->enabled = status == ESP_OK;
  }
  portENTER_CRITICAL(&ctx->mux);
  slot.raw.arm_us = esp_timer_get_time();
  slot.raw.prior_gap_us = ctx->last_seal_us ? slot.raw.arm_us - ctx->last_seal_us : 0;
  portEXIT_CRITICAL(&ctx->mux);
  if (status == ESP_OK)
    status = rmt_receive(this->channel_, this->dma_, RawOokWindow::CAPACITY * sizeof(rmt_symbol_word_t), &config);
  portENTER_CRITICAL(&ctx->mux);
  slot.arm_after_us = esp_timer_get_time();
  if (status != ESP_OK) {
    ctx->driver_error = status;
    ctx->stop = true;
  }
  portEXIT_CRITICAL(&ctx->mux);
  return status == ESP_OK;
}

bool IRAM_ATTR BufferedOokCapture::on_receive_(rmt_channel_handle_t, const rmt_rx_done_event_data_t* event, void* arg) {
  auto* ctx = static_cast<Context*>(arg);
  BaseType_t woken = pdFALSE;
  portENTER_CRITICAL_ISR(&ctx->mux);
  auto& slot = ctx->slots[ctx->filling];
  if (slot.owner == Ownership::FILLING) {
    auto& raw = slot.raw;
    raw.callback_count++;
    raw.callback_us = esp_timer_get_time();
    if (event->received_symbols == nullptr || event->num_symbols > RawOokWindow::CAPACITY) {
      raw.saturated = true;
      ctx->callback_losses++;
      ctx->stop = true;
    } else {
      for (size_t index = 0; index < event->num_symbols; index++) {
        if (raw.count == RawOokWindow::CAPACITY) {
          raw.saturated = true;
          break;
        }
        const auto& symbol = event->received_symbols[index];
        raw.words[raw.count++] = RawOokWindow::pack(symbol.duration0, symbol.level0, symbol.duration1, symbol.level1);
      }
    }
    raw.final_seen = event->flags.is_last;
    raw.saturated = raw.saturated || raw.count == RawOokWindow::CAPACITY;
    if (raw.final_seen || raw.saturated) {
      slot.owner = Ownership::SEALED;
      vTaskNotifyGiveFromISR(ctx->task, &woken);
    }
  } else {
    slot.raw.ignored_callbacks++;
    if (event->num_symbols <= RawOokWindow::CAPACITY)
      slot.raw.ignored_symbols += event->num_symbols;
  }
  portEXIT_CRITICAL_ISR(&ctx->mux);
  return woken == pdTRUE;
}

bool BufferedOokCapture::stop_driver_() {
  auto* ctx = this->context_;
  if (ctx != nullptr && ctx->enabled) {
    const auto status = rmt_disable(this->channel_);
    if (status != ESP_OK) {
      portENTER_CRITICAL(&ctx->mux);
      ctx->driver_error = status;
      portEXIT_CRITICAL(&ctx->mux);
      return false;
    }
    ctx->enabled = false;
  }
  return true;
}

void BufferedOokCapture::task_entry_(void* arg) {
  auto* self = static_cast<BufferedOokCapture*>(arg);
  ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
  self->acquire_();
  auto* ctx = self->context_;
  ctx->stack_free_bytes = uxTaskGetStackHighWaterMark(nullptr);
  ctx->ended_us = esp_timer_get_time();
  xSemaphoreGive(ctx->finished);
  // No access to self/ctx after acknowledgement. Main owns deletion/cleanup.
  vTaskSuspend(nullptr);
}

void BufferedOokCapture::acquire_() {
  auto* ctx = this->context_;
  bool armed = this->arm_(0);
  xSemaphoreGive(ctx->started);
  while (armed) {
    ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(5));
    const int64_t now = esp_timer_get_time();
    portENTER_CRITICAL(&ctx->mux);
    const bool deadline = now - this->started_us_ >= static_cast<int64_t>(this->limits_.session_ms) * 1000;
    const bool stop = ctx->stop || deadline;
    const uint8_t index = ctx->filling;
    auto& slot = ctx->slots[index];
    const bool sealed = slot.owner == Ownership::SEALED;
    portEXIT_CRITICAL(&ctx->mux);
    if (!sealed && !stop)
      continue;
    if (!this->stop_driver_())
      break; // Keep all memory until hardware shutdown can be confirmed.
    portENTER_CRITICAL(&ctx->mux);
    slot.raw.poll_us = esp_timer_get_time();
    slot.raw.seal_us = slot.raw.poll_us;
    slot.raw.timed_out = deadline && !sealed;
    ctx->last_seal_us = slot.raw.seal_us;
    bool publish = slot.raw.count > 0;
    slot.owner = publish ? Ownership::READY : Ownership::FREE;
    uint8_t next = SLOT_COUNT;
    for (uint8_t candidate = 0; candidate < SLOT_COUNT; candidate++)
      if (ctx->slots[candidate].owner == Ownership::FREE) {
        next = candidate;
        break;
      }
    if (!stop && next == SLOT_COUNT) {
      ctx->pool_losses++;
      ctx->stop = true;
    }
    portEXIT_CRITICAL(&ctx->mux);
    // Rearm before publishing work to the lower-priority main-loop consumer.
    armed = !stop && next != SLOT_COUNT && this->arm_(next);
    if (publish && xQueueSend(ctx->ready, &index, 0) != pdTRUE) {
      portENTER_CRITICAL(&ctx->mux);
      ctx->queue_losses++;
      ctx->stop = true;
      portEXIT_CRITICAL(&ctx->mux);
      armed = false;
    }
    if (!armed)
      break;
  }
  this->stop_driver_();
}

void BufferedOokCapture::request_stop() {
  auto* ctx = this->context_;
  if (ctx == nullptr)
    return;
  portENTER_CRITICAL(&ctx->mux);
  ctx->stop = true;
  auto task = ctx->task;
  portEXIT_CRITICAL(&ctx->mux);
  if (task != nullptr && !this->stopped_)
    xTaskNotifyGive(task);
}

void BufferedOokCapture::process() {
  auto* ctx = this->context_;
  if (ctx == nullptr)
    return;
  if (ctx->task == nullptr)
    this->stopped_ = true; // Failed startup with resources retained for retry.
  else if (!this->stopped_ && xSemaphoreTake(ctx->finished, 0) == pdTRUE)
    this->stopped_ = true;
  uint8_t index;
  if (ctx->ready != nullptr && xQueueReceive(ctx->ready, &index, 0) == pdTRUE) {
    auto& slot = ctx->slots[index];
    portENTER_CRITICAL(&ctx->mux);
    slot.owner = Ownership::PROCESSING;
    portEXIT_CRITICAL(&ctx->mux);
    const bool plausible = slot.raw.has_frame_prefix();
    const bool selected = plausible || (this->limits_.capture_rejected && this->rejected_ < this->limits_.max_rejected);
    if (selected && this->retained_ < this->limits_.max_exports) {
      auto& record = this->records_[this->retained_++];
      record.raw = slot.raw;
      record.history = *this->history_;
      record.arm_after_us = slot.arm_after_us;
      record.gpio_anchor_us = slot.raw.arm_us;
#ifdef USE_PROFLAME2_GPIO_EDGE_DIAGNOSTICS
      record.gpio_anchor_us =
          this->observer_.snapshot_for_burst(slot.raw.arm_us, slot.raw.seal_us, record.gpio_anchor_observed);
      record.gpio = this->observer_.saved_snapshot();
#endif
      if (!plausible)
        this->rejected_++;
    } else if (selected) {
      ctx->retention_losses++;
    } else {
      this->suppressed_++;
    }
    this->history_->append(slot.raw);
    portENTER_CRITICAL(&ctx->mux);
    slot.owner = Ownership::FREE;
    if (this->retained_ >= this->limits_.max_exports) {
      ctx->limit = true;
      ctx->stop = true;
    }
    portEXIT_CRITICAL(&ctx->mux);
    if (ctx->limit)
      this->request_stop();
    return;
  }
  if (this->stopped_ && !this->hardware_released_) {
    if (!this->end_memory_logged_) {
      this->memory_("capture_end");
      ESP_LOGI("proflame2.raw", "PFSTOP1 session=%" PRIu32 " ended_us=%" PRId64, this->session_, ctx->ended_us);
      this->end_memory_logged_ = true;
    }
    this->hardware_released_ = this->release_hardware_();
  }
}

bool BufferedOokCapture::release_hardware_() {
  if (!this->stop_driver_())
    return false;
  if (this->channel_ != nullptr) {
    const auto status = rmt_del_channel(this->channel_);
    if (status != ESP_OK) {
      this->context_->driver_error = status;
      return false;
    }
    this->channel_ = nullptr;
  }
#ifdef USE_PROFLAME2_GPIO_EDGE_DIAGNOSTICS
  std::string error;
  if (!this->observer_.end(error)) {
    ESP_LOGE("proflame2.raw", "Buffered capture shutdown pending: %s", error.c_str());
    return false;
  }
#endif
  return true;
}

void BufferedOokCapture::export_tick() {
  if (this->context_ == nullptr || !this->stopped_ || !this->hardware_released_)
    return;
  if (this->exported_ < this->retained_) {
    this->export_record_();
    return;
  }
  const auto* ctx = this->context_;
  const bool failed =
      ctx->driver_error || ctx->pool_losses || ctx->queue_losses || ctx->callback_losses || ctx->retention_losses;
  ESP_LOGI("proflame2.raw",
           "PFACQ1 session=%" PRIu32 " pool_losses=%" PRIu32 " queue_losses=%" PRIu32 " callback_losses=%" PRIu32
           " retention_losses=%" PRIu32 " driver_error=%d stack_free_bytes=%" PRIu32 " ended_us=%" PRId64,
           this->session_, ctx->pool_losses, ctx->queue_losses, ctx->callback_losses, ctx->retention_losses,
           ctx->driver_error, ctx->stack_free_bytes, ctx->ended_us);
  const int64_t end = ctx->ended_us;
  this->release_();
  if (this->context_ != nullptr)
    return;
  if (this->opened_) {
    ESP_LOGI("proflame2.raw",
             "PFSESSION session=%" PRIu32 " state=%s exports=%u suppressed=%" PRIu32 " end_us=%" PRId64, this->session_,
             failed ? "failed" : "paused", static_cast<unsigned>(this->exported_), this->suppressed_, end);
    this->opened_ = false;
  }
}

void BufferedOokCapture::release_() {
  if (this->context_ != nullptr && this->context_->task != nullptr && !this->stopped_)
    return;
  if (!this->release_hardware_())
    return;
  auto* ctx = this->context_;
  if (ctx != nullptr) {
    if (ctx->task != nullptr)
      vTaskDelete(ctx->task);
    if (ctx->ready != nullptr)
      vQueueDelete(ctx->ready);
    if (ctx->started != nullptr)
      vSemaphoreDelete(ctx->started);
    if (ctx->finished != nullptr)
      vSemaphoreDelete(ctx->finished);
    ctx->~Context();
    heap_caps_free(ctx);
    this->context_ = nullptr;
  }
  heap_caps_free(this->dma_);
  this->dma_ = nullptr;
  heap_caps_free(this->records_);
  this->records_ = nullptr;
  heap_caps_free(this->history_);
  this->history_ = nullptr;
  if (this->memory_open_) {
    this->memory_("released");
    this->memory_open_ = false;
  }
}

void BufferedOokCapture::memory_(const char* phase) const {
  multi_heap_info_t internal{}, dma{}, psram{};
  heap_caps_get_info(&internal, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
  heap_caps_get_info(&dma, MALLOC_CAP_INTERNAL | MALLOC_CAP_DMA | MALLOC_CAP_8BIT);
  heap_caps_get_info(&psram, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
  ESP_LOGI("proflame2.memory",
           "PFMEM1 session=%" PRIu32 " phase=%s internal_free=%u internal_largest=%u internal_min=%u"
           " dma_free=%u dma_largest=%u dma_min=%u psram_free=%u psram_largest=%u psram_min=%u",
           this->session_, phase, static_cast<unsigned>(internal.total_free_bytes),
           static_cast<unsigned>(internal.largest_free_block), static_cast<unsigned>(internal.minimum_free_bytes),
           static_cast<unsigned>(dma.total_free_bytes), static_cast<unsigned>(dma.largest_free_block),
           static_cast<unsigned>(dma.minimum_free_bytes), static_cast<unsigned>(psram.total_free_bytes),
           static_cast<unsigned>(psram.largest_free_block), static_cast<unsigned>(psram.minimum_free_bytes));
}

void BufferedOokCapture::export_record_() {
  const auto& record = this->records_[this->exported_];
  const auto& raw = record.raw;
  if (this->export_phase_ == 0) {
#ifdef USE_PROFLAME2_GPIO_EDGE_DIAGNOSTICS
    if (!this->gpio_export_done_) {
      this->gpio_export_done_ = this->observer_.export_snapshot(this->session_, raw.sequence, record.gpio);
      return;
    }
#endif
    ESP_LOGI("proflame2.raw",
             "PFBOUND1 session=%" PRIu32 " id=%" PRIu32 " arm_before_us=%" PRId64 " arm_after_us=%" PRId64
             " gpio_anchor_us=%" PRId64 " gpio_anchor_observed=%u",
             this->session_, raw.sequence, raw.arm_us, record.arm_after_us, record.gpio_anchor_us,
             static_cast<unsigned>(record.gpio_anchor_observed));
    this->export_phase_ = 1;
    return;
  }
  const auto& history = record.history;
  const bool historical = this->export_segment_ < history.segment_count;
  const RawOokMetadata& meta =
      historical ? static_cast<const RawOokMetadata&>(history.segments[this->export_segment_]) : raw;
  const uint32_t* data = historical ? history.words.data() + this->history_offset_ : raw.words.data();
  const char* prefix = historical ? "PFHIST1" : "PFRAW2";
  if (this->export_phase_ == 1) {
    if (historical)
      ESP_LOGI("proflame2.raw", "PFHIST1 parent=%" PRIu32 " source_offset=%u", raw.sequence,
               static_cast<unsigned>(history.segments[this->export_segment_].source_offset));
    else
      ESP_LOGI("proflame2.raw",
               "PFPRE1 session=%" PRIu32 " id=%" PRIu32 " requested_us=75000 duration_us=%" PRIu32
               " segments=%u short=%u limited=%u",
               this->session_, raw.sequence, history.duration_us, static_cast<unsigned>(history.segment_count),
               static_cast<unsigned>(history.duration_us < RawOokHistory::TARGET_US),
               static_cast<unsigned>(history.limited));
    this->export_phase_ = 2;
  } else if (this->export_phase_ == 2) {
    ESP_LOGI("proflame2.raw",
             "%s session=%" PRIu32 " id=%" PRIu32 " count=%u saturated=%u poll_us=%" PRId64 " rearm_us=%" PRId64
             " resolution_hz=1000000 filter_ns=%" PRIu32 " idle_ns=30000000 checksum=%08" PRIx32,
             prefix, this->session_, meta.sequence, static_cast<unsigned>(meta.count),
             static_cast<unsigned>(meta.saturated), meta.poll_us, meta.arm_us, this->limits_.filter_ns,
             RawOokWindow::checksum(data, meta.count));
    this->export_phase_ = 3;
  } else if (this->export_phase_ == 3) {
    ESP_LOGI("proflame2.raw",
             "%s id=%" PRIu32 " arm_us=%" PRId64 " callback_us=%" PRId64 " seal_us=%" PRId64 " gap_us=%" PRId64
             " callbacks=%" PRIu32 " queue_losses=%" PRIu32 " ignored_callbacks=%" PRIu32 " ignored_symbols=%" PRIu32
             " final=%u timeout=%u",
             prefix, meta.sequence, meta.arm_us, meta.callback_us, meta.seal_us, meta.prior_gap_us, meta.callback_count,
             meta.queue_losses, meta.ignored_callbacks, meta.ignored_symbols, static_cast<unsigned>(meta.final_seen),
             static_cast<unsigned>(meta.timed_out));
    this->export_phase_ = 4;
  } else if (this->export_offset_ < meta.count) {
    char chunk[129];
    size_t length = 0;
    for (size_t index = this->export_offset_; index < meta.count && index < this->export_offset_ + 16; index++) {
      snprintf(chunk + length, sizeof(chunk) - length, "%08" PRIx32, data[index]);
      length += 8;
    }
    ESP_LOGI("proflame2.raw", "%s id=%" PRIu32 " offset=%u words=%s", prefix, meta.sequence,
             static_cast<unsigned>(this->export_offset_), chunk);
    this->export_offset_ += 16;
  } else {
    ESP_LOGI("proflame2.raw", "%s id=%" PRIu32 " end=1", prefix, meta.sequence);
    this->export_phase_ = 1;
    this->export_offset_ = 0;
    if (historical) {
      this->history_offset_ += meta.count;
      this->export_segment_++;
    } else {
      this->exported_++;
      this->export_phase_ = this->export_segment_ = this->history_offset_ = 0;
      this->gpio_export_done_ = false;
#ifdef USE_PROFLAME2_GPIO_EDGE_DIAGNOSTICS
      this->observer_.reset_export();
#endif
    }
  }
}

} // namespace proflame2_tembed
} // namespace esphome
#endif
