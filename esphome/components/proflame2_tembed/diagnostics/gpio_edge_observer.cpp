#include "gpio_edge_observer.h"

#ifdef USE_PROFLAME2_GPIO_EDGE_DIAGNOSTICS
#include <cstdio>
#include <inttypes.h>
#include <new>
#include "driver/gpio.h"
#include "esp_heap_caps.h"
#include "esp_ipc.h"
#include "esp_timer.h"
#include "hal/gpio_ll.h"
#include "soc/gpio_struct.h"
#include "esphome/core/log.h"

namespace esphome {
namespace proflame2_tembed {

bool GPIOEdgeObserver::begin(InternalGPIOPin* pin, std::string& error) {
  if (!this->end(error))
    return false;
  if (pin == nullptr || !GPIO_IS_VALID_GPIO(pin->get_pin())) {
    error = "gpio_observer_pin_invalid";
    return false;
  }
  void* memory = heap_caps_calloc(1, sizeof(Context), MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
  if (memory == nullptr) {
    error = "gpio_observer_allocation_failed";
    return false;
  }
  this->context_ = new (memory) Context();
  auto& ring = this->context_->ring;
  ring.gpio_num = pin->get_pin();
  ring.last_level = gpio_get_level(static_cast<gpio_num_t>(ring.gpio_num));
  ring.started_us = esp_timer_get_time();
  ring.budget_start_us = ring.started_us;
  // Let ESPHome manage the shared service, including its installation flag.
  // Do not uninstall/upgrade the service or change the pin's direction/pulls.
  pin->attach_interrupt<void>(&GPIOEdgeObserver::on_edge_, this->context_, gpio::INTERRUPT_ANY_EDGE);
  this->handler_attached_ = true;
  this->needs_quiesce_ = true;
  // attach_interrupt has no return value. Re-register our own handler through
  // the checked IDF API so failed service installation cannot silently pass.
  const auto result =
      gpio_isr_handler_add(static_cast<gpio_num_t>(ring.gpio_num), &GPIOEdgeObserver::on_edge_, this->context_);
  if (result != ESP_OK) {
    error = "gpio_observer_handler_failed";
    return false;
  }
  portENTER_CRITICAL(&this->context_->mux);
  ring.ready_us = esp_timer_get_time();
  ring.budget_start_us = ring.ready_us;
  ring.active = true;
  portEXIT_CRITICAL(&this->context_->mux);
  return true;
}

bool GPIOEdgeObserver::end(std::string& error) {
  if (this->context_ == nullptr)
    return true;
  auto* context = this->context_;
  portENTER_CRITICAL(&context->mux);
  context->ring.active = false;
  portEXIT_CRITICAL(&context->mux);
  if (this->handler_attached_) {
    const auto pin = static_cast<gpio_num_t>(context->ring.gpio_num);
    if (gpio_intr_disable(pin) != ESP_OK) {
      error = "gpio_observer_disable_failed";
      return false;
    }
    const auto result = gpio_isr_handler_remove(pin);
    // INVALID_STATE means there is no service/handler after failed setup.
    if (result != ESP_OK && result != ESP_ERR_INVALID_STATE) {
      error = "gpio_observer_remove_failed";
      return false;
    }
    this->handler_attached_ = false;
  }
  if (this->needs_quiesce_) {
    // Disabling/removing a handler alone does not prove that an ISR on another
    // core has returned. Let an IPC task execute on each core before freeing.
    for (int core = 0; core < portNUM_PROCESSORS; core++) {
      if (esp_ipc_call_blocking(core, &GPIOEdgeObserver::quiesce_, nullptr) != ESP_OK) {
        error = "gpio_observer_quiesce_failed";
        return false;
      }
    }
    this->needs_quiesce_ = false;
  }
  context->~Context();
  heap_caps_free(context);
  this->context_ = nullptr;
  return true;
}

void IRAM_ATTR GPIOEdgeObserver::on_edge_(void* arg) {
  auto* context = static_cast<Context*>(arg);
  portENTER_CRITICAL_ISR(&context->mux);
  const bool stop = context->ring.record(esp_timer_get_time(), gpio_ll_get_level(&GPIO, context->ring.gpio_num));
  if (stop)
    gpio_intr_disable(static_cast<gpio_num_t>(context->ring.gpio_num));
  portEXIT_CRITICAL_ISR(&context->mux);
}

void GPIOEdgeObserver::snapshot(int64_t rmt_arm_us) {
  auto* context = this->context_;
  auto& saved = context->saved;
  saved.count = 0;
  saved.raced = false;
  saved.requested_us = rmt_arm_us > 75000 ? rmt_arm_us - 75000 : 0;
  portENTER_CRITICAL(&context->mux);
  saved.snapshot_us = esp_timer_get_time();
  saved.total = context->ring.total;
  saved.started_us = context->ring.started_us;
  saved.ready_us = context->ring.ready_us;
  saved.storm = context->ring.storm;
  saved.repeated_levels = context->ring.repeated_levels;
  portEXIT_CRITICAL(&context->mux);
  const uint64_t oldest = saved.total > CAPACITY ? saved.total - CAPACITY : 0;
  saved.first_seq = saved.total;
  saved.clipped = saved.requested_us < saved.ready_us;
  // Copy one entry per short lock, not a 16 KiB memcpy that blocks interrupts.
  // Detect overwritten entries rather than silently copying a mixed ring.
  for (uint64_t sequence = oldest; sequence < saved.total; sequence++) {
    portENTER_CRITICAL(&context->mux);
    const bool lost = context->ring.total - sequence > CAPACITY;
    const uint64_t edge = context->ring.edges[sequence & (CAPACITY - 1)];
    portEXIT_CRITICAL(&context->mux);
    if (lost) {
      saved.raced = true;
      saved.count = 0;
      saved.first_seq = saved.total;
      break;
    }
    if (sequence == oldest && oldest && static_cast<int64_t>(edge >> 1U) > saved.requested_us)
      saved.clipped = true;
    if (static_cast<int64_t>(edge >> 1U) >= saved.requested_us) {
      if (saved.count == 0)
        saved.first_seq = sequence;
      saved.edges[saved.count++] = edge;
    }
  }
  this->export_phase_ = 0;
  this->export_offset_ = 0;
}

int64_t GPIOEdgeObserver::snapshot_for_burst(int64_t arm_us, int64_t seal_us, bool& observed) {
  this->snapshot(0); // Inspect the whole bounded ring, including a preceding LOW edge.
  const auto& saved = this->context_->saved;
  int64_t anchor = arm_us;
  observed = false;
  // An observed GDO0 rising edge after >=30 ms LOW is a burst-start hint,
  // not an RF-onset measurement. Keep 75 ms before that hint, even if RMT
  // was armed long before the remote button press. Never invent gap samples.
  for (size_t index = 1; index < saved.count; index++) {
    const int64_t before = saved.edges[index - 1] >> 1U;
    const int64_t after = saved.edges[index] >> 1U;
    if (!(saved.edges[index - 1] & 1U) && (saved.edges[index] & 1U) && after - before >= 30000 && after <= seal_us &&
        after >= arm_us - 75000) {
      anchor = after;
      observed = true;
      if (after >= arm_us)
        break;
    }
  }
  this->snapshot(anchor);
  return anchor;
}

bool GPIOEdgeObserver::export_snapshot(uint32_t session, uint32_t capture) {
  return this->export_snapshot(session, capture, this->context_->saved);
}

bool GPIOEdgeObserver::export_snapshot(uint32_t session, uint32_t capture, const Snapshot& saved) {
  if (this->export_phase_ == 0) {
    ESP_LOGI("proflame2.raw", "PFEDGE1 session=%" PRIu32 " id=%" PRIu32 " count=%u checksum=%08" PRIx32, session,
             capture, static_cast<unsigned>(saved.count), saved.checksum());
    this->export_phase_ = 1;
  } else if (this->export_phase_ == 1) {
    ESP_LOGI("proflame2.raw",
             "PFEDGE1 id=%" PRIu32 " request_us=%" PRId64 " start_us=%" PRId64 " ready_us=%" PRId64
             " snapshot_us=%" PRId64 " first_seq=%" PRIu64 " total=%" PRIu64
             " clipped=%u raced=%u storm=%u repeats=%" PRIu32,
             capture, saved.requested_us, saved.started_us, saved.ready_us, saved.snapshot_us, saved.first_seq,
             saved.total, static_cast<unsigned>(saved.clipped), static_cast<unsigned>(saved.raced),
             static_cast<unsigned>(saved.storm), saved.repeated_levels);
    this->export_phase_ = 2;
  } else if (this->export_offset_ < saved.count) {
    char chunk[8 * 16 + 1];
    size_t length = 0;
    for (size_t index = this->export_offset_; index < saved.count && index < this->export_offset_ + 8; index++) {
      snprintf(chunk + length, sizeof(chunk) - length, "%016" PRIx64, saved.edges[index]);
      length += 16;
    }
    ESP_LOGI("proflame2.raw", "PFEDGE1 id=%" PRIu32 " offset=%u edges=%s", capture,
             static_cast<unsigned>(this->export_offset_), chunk);
    this->export_offset_ += 8;
  } else {
    ESP_LOGI("proflame2.raw", "PFEDGE1 id=%" PRIu32 " end=1", capture);
    return true;
  }
  return false;
}

} // namespace proflame2_tembed
} // namespace esphome
#endif
