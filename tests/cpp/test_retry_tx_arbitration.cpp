// The runner inserts real runtime value types and component method definitions.
// FreeRTOS queues and board/UI side effects are simulated, not the TX policy.
#include "radio_cc1101.h"
#include "delayed_tx_retry.h"
#include "tx_controller.h"
#include "esphome/core/log.h"
#include <cassert>
#include <cstring>
#include <deque>
#include <inttypes.h>
#include <string>
#include <vector>

struct FakeQueue {
  size_t item_size{0};
  bool reject_send{false};
  std::deque<std::vector<uint8_t>> items;
};
using QueueHandle_t = FakeQueue*;
using TaskHandle_t = void*;
using UBaseType_t = unsigned;
static constexpr int pdTRUE = 1;
static constexpr int tskIDLE_PRIORITY = 0;
static int xQueueSend(FakeQueue* queue, const void* item, int) {
  if (queue->reject_send || !queue->items.empty())
    return 0;
  const auto* bytes = static_cast<const uint8_t*>(item);
  queue->items.emplace_back(bytes, bytes + queue->item_size);
  return pdTRUE;
}
static int xQueueReceive(FakeQueue* queue, void* item, int) {
  if (queue->items.empty())
    return 0;
  std::memcpy(item, queue->items.front().data(), queue->item_size);
  queue->items.pop_front();
  return pdTRUE;
}

namespace esphome {
static uint32_t test_millis = 0;
static uint32_t millis() {
  return test_millis;
}
namespace proflame2_tembed {
static const char* const TAG = "test";
static const char* const PROFLAME_BUILD_MARKER = "test";
enum class RadioRuntimeState { IDLE, TX_ACTIVE, ERROR };
enum class RadioRuntimeStartReason { TX_REQUEST, DIAGNOSTIC };
enum class RadioRuntimeInitState { READY, FAILED };
static const char* runtime_state_to_string_(RadioRuntimeState) {
  return "test";
}
static const char* tx_mode_to_string_(TXMode) {
  return "test";
}
static const char* native_group_timing_profile_to_string_(NativeGroupTimingProfile) {
  return "test";
}
static const char* native_group_repeat_boundary_mode_to_string_(NativeGroupRepeatBoundaryMode) {
  return "test";
}
static std::string format_hex_byte_(uint8_t value) {
  return std::to_string(value);
}
static void log_air_payload_symbols_(const std::string&, const std::vector<uint8_t>&, uint32_t, uint32_t) {}

class Proflame2TEmbedComponent {
public:
  struct RadioRuntime;
  struct PendingDisplayIntent {
    bool valid{false};
    std::string request_id;
    int power{-1}, flame{-1}, fan{-1}, light{-1}, pilot{-1}, thermostat{-1}, front{-1}, aux{-1};
    std::string action_label;
  };
  struct Display {
    std::string fireplace_name, last_action_text, active_operation_title, active_operation_detail;
    uint32_t last_action_millis{0}, active_operation_expires_millis{0};
    bool active_operation{false}, api_connected{true}, learn_active{false};
  } display_;
  RadioRuntime* radio_runtime_{nullptr};
  RadioRuntimeState radio_runtime_state_{RadioRuntimeState::IDLE};
  RadioRuntimeInitState radio_runtime_init_state_{RadioRuntimeInitState::READY};
  bool tx_in_progress_{false}, display_wake_on_activity_{false};
  bool rx_rmt_pulse_capture_enabled_{true}, rx_rmt_pulse_paused_for_tx_{false};
  bool rx_fifo_capture_enabled_{false}, rx_fifo_paused_for_tx_{false}, rx_fifo_capture_configured_{false};
  uint8_t tx_repeat_count_{5}, diagnostic_repeat_count_override_{0};
  uint32_t payload_bit_length_override_{0}, last_payload_bit_length_{0}, last_payload_length_{0};
  uint32_t last_request_repeat_count_{0}, last_tx_elapsed_ms_{0}, tx_failure_count_{0}, tx_success_count_{0};
  uint32_t inter_frame_gap_us_{0}, post_frame_idle_gap_us_{0}, pre_burst_low_us_{834}, pre_frame_low_us_{0};
  TXMode tx_mode_{TXMode::PROFLAME_NATIVE_GROUPS};
  NativeGroupTimingProfile native_group_timing_profile_{NativeGroupTimingProfile::NATIVE_REMOTE};
  NativeGroupRepeatBoundaryMode native_group_repeat_boundary_mode_{NativeGroupRepeatBoundaryMode::CONTINUOUS_TX};
  std::string last_request_id_, last_payload_hex_, last_tx_path_, last_error_, last_tx_result_, status_text_;
  std::string cc1101_partnum_, cc1101_version_, last_marcstate_before_tx_, last_marcstate_after_tx_;
  PendingDisplayIntent pending_display_intent_;
  std::string applied_request;
  int applied_light{-1};
  uint32_t settle_us{20000};
  uint32_t restores{0};
  bool clobber_worker_event_on_restore{false};
  bool tx(const std::string&, const std::string&, uint32_t, uint8_t, const std::string&, int, int, int, int, int, int,
          int, int, const std::string&, const std::string&);
  bool enqueue_tx_(const std::string&, const std::string&, std::vector<uint8_t>&&, uint32_t, uint8_t,
                   const std::string&);
  void consume_radio_runtime_events_();
  void process_delayed_retransmission_();
  void dispatch_deferred_tx_();
  void pause_rx_for_tx_();
  void cancel_delayed_retransmission_(const char*);
  bool ensure_radio_runtime_(RadioRuntimeStartReason) {
    return true;
  }
  bool tx_debug_logging_enabled_() const {
    return false;
  }
  uint32_t get_pre_tx_settle_delay_us() const {
    return settle_us;
  }
  void clear_deferred_debug_trace_() {}
  void publish_telemetry_() {}
  void refresh_status_text_();
  std::string get_display_mode_badge_text() const;
  void update_rx_runtime_display_state_() {}
  void mark_display_activity_(bool) {}
  void update_display_runtime_state_(const std::string&, const std::string&, uint32_t) {}
  void log_rf_path_for_tx_(uint32_t, uint32_t, uint8_t) const {}
  void store_deferred_debug_trace_(const TXTimingDiagnostics&, TXMode) {}
  void increment_rx_suppressed_for_tx_() {}
  void set_rmt_pulse_capture_enabled_(bool value) {
    rx_rmt_pulse_capture_enabled_ = value;
  }
  void strobe_(uint8_t) {}
  void log_radio_registers(const RadioTraceRecord&, const char*, const char*) {}
  void clear_pending_display_intent_() {
    pending_display_intent_ = PendingDisplayIntent{};
  }
  void apply_pending_display_intent_(const std::string& request_id) {
    if (pending_display_intent_.valid && pending_display_intent_.request_id == request_id) {
      applied_request = request_id;
      applied_light = pending_display_intent_.light;
      clear_pending_display_intent_();
    }
  }
  void restore_rx_after_tx_if_needed_();
};

// INSERT_PRODUCTION_RUNTIME_TYPES
// INSERT_PRODUCTION_METHODS

void Proflame2TEmbedComponent::restore_rx_after_tx_if_needed_() {
  ++restores;
  if (clobber_worker_event_on_restore) {
    // Simulate the worker reusing its scratch space while main handles a copy.
    radio_runtime_->event_buffer.ok = false;
    copy_string_(radio_runtime_->event_buffer.request_id, "worker_reused_buffer");
  }
  if (rx_rmt_pulse_paused_for_tx_) {
    rx_rmt_pulse_paused_for_tx_ = false;
    rx_rmt_pulse_capture_enabled_ = true;
  }
}

struct Bench {
  Proflame2TEmbedComponent controller;
  Proflame2TEmbedComponent::RadioRuntime runtime;
  FakeQueue commands{sizeof(RadioRuntimeCommand), false, {}}, events{sizeof(RadioRuntimeEvent), false, {}};
  Bench() {
    controller.radio_runtime_ = &runtime;
    runtime.command_queue = &commands;
    runtime.event_queue = &events;
    runtime.retry.set_enabled(true);
    test_millis = 0;
  }
  bool send(const char* id, const std::string& payload, uint32_t bits, int light) {
    return controller.tx(id, payload, bits, 5, "test", 1, 1, 0, light, 0, 0, 0, 0, id, "test");
  }
  RadioRuntimeCommand start_worker() {
    RadioRuntimeCommand command;
    assert(xQueueReceive(&commands, &command, 0) == pdTRUE);
    return command;
  }
  void finish_worker(const RadioRuntimeCommand& command, bool ok = true) {
    auto& result = runtime.event_buffer;
    result = RadioRuntimeEvent{};
    result.kind = RadioRuntimeEventKind::TX_COMPLETE;
    result.ok = ok;
    result.is_retransmission = command.is_retransmission;
    result.retry_generation = command.retry_generation;
    copy_string_(result.request_id, std::string(command.request_id));
    copy_string_(result.air_payload_hex, std::string(command.air_payload_hex));
    result.payload_length = command.payload_length;
    result.ha_payload_bit_length = command.ha_payload_bit_length;
    result.payload_bit_length = command.payload_bit_length;
    result.repeat_count = command.repeat_count;
    result.finished_ms = test_millis;
    result.elapsed_ms = 421;
    result.pre_tx_settle_delay_us = command.pre_tx_settle_delay_us;
    if (!ok)
      copy_string_(result.radio_error, "simulated_failure");
    assert(xQueueSend(&events, &result, 0) == pdTRUE);
  }
  RadioRuntimeCommand start_retry(const std::string& payload, uint32_t bits) {
    assert(send("original", payload, bits, 0));
    auto original = start_worker();
    finish_worker(original);
    controller.consume_radio_runtime_events_();
    test_millis = 2000;
    controller.process_delayed_retransmission_();
    auto retry = start_worker();
    assert(retry.is_retransmission);
    return retry;
  }
};

} // namespace proflame2_tembed
} // namespace esphome

using namespace esphome::proflame2_tembed;
int main(int argc, char** argv) {
  assert(argc == 4);
  const std::string payload = argv[1];
  const uint32_t bits = std::stoul(argv[2]);
  const std::string new_payload = argv[3];
  assert(payload != new_payload);
  // A replacement rejected before admission must preserve the accepted retry.
  for (bool reject_queue : {false, true}) {
    Bench b;
    assert(b.send("original", payload, bits, 6));
    b.finish_worker(b.start_worker());
    b.controller.consume_radio_runtime_events_();
    assert(b.runtime.retry.pending());
    b.commands.reject_send = reject_queue;
    assert(!b.send("rejected", reject_queue ? new_payload : "invalid", bits, 0));
    assert(b.runtime.retry.pending());
    b.commands.reject_send = false;
    esphome::test_millis = 2000;
    b.controller.process_delayed_retransmission_();
    const auto retry = b.start_worker();
    assert(retry.is_retransmission && std::string(retry.request_id) == "original");
    assert(std::string(retry.air_payload_hex) == payload);
  }
  // Queue completion must survive worker scratch-buffer reuse during RX setup.
  {
    Bench b;
    assert(&b.runtime.event_buffer != &b.runtime.completion_buffer);
    b.controller.clobber_worker_event_on_restore = true;
    assert(b.send("original", payload, bits, 6));
    b.finish_worker(b.start_worker());
    b.controller.consume_radio_runtime_events_();
    assert(b.controller.last_request_id_ == "original");
    assert(b.controller.last_tx_result_ == "ok");
    assert(b.controller.applied_request == "original");
    assert(std::string(b.runtime.event_buffer.request_id) == "worker_reused_buffer");
    // An empty poll cannot process the previous completion a second time.
    b.controller.consume_radio_runtime_events_();
    assert(b.controller.tx_success_count_ == 1);
  }
  // Reproduce the log's busy window: worker finished, event not yet consumed.
  {
    Bench b;
    auto retry = b.start_retry(payload, bits);
    b.finish_worker(retry);
    assert(b.send("next", new_payload, bits, 6));
    auto next = b.start_worker();
    assert(!next.is_retransmission && std::string(next.request_id) == "next");
    assert(std::string(next.air_payload_hex) == new_payload);
    assert(b.controller.tx_failure_count_ == 0);
    b.finish_worker(next);
    b.controller.consume_radio_runtime_events_();
    assert(b.controller.applied_request == "next" && b.controller.applied_light == 6);
  }
  // Also drain an original completion before mutating the next display intent.
  {
    Bench b;
    assert(b.send("first", payload, bits, 1));
    b.finish_worker(b.start_worker());
    assert(b.send("next", payload, bits, 6));
    assert(b.controller.applied_request == "first" && b.controller.applied_light == 1);
  }
  // A real in-flight retry is allowed to finish; one new command waits off-radio.
  for (bool retry_ok : {false, true}) {
    Bench b;
    auto retry = b.start_retry(payload, bits);
    assert(b.send("waiting", new_payload, bits, 6));
    assert(b.commands.items.empty());
    assert(b.runtime.deferred_tx_pending && !b.controller.rx_rmt_pulse_capture_enabled_);
    assert(b.controller.last_tx_result_ == "queued");
    // A second waiting request is bounded/rejected, not substituted silently.
    assert(!b.send("overflow", payload, bits, 1));
    b.controller.refresh_status_text_();
    assert(b.controller.get_display_mode_badge_text() == "ERR");
    b.controller.settle_us = 0;
    b.finish_worker(retry, retry_ok);
    b.controller.consume_radio_runtime_events_();
    auto waiting = b.start_worker();
    assert(std::string(waiting.request_id) == "waiting" && !waiting.is_retransmission);
    assert(waiting.pre_tx_settle_delay_us == 20000 && waiting.payload_bit_length == bits);
    assert(waiting.repeat_count == 5 && std::string(waiting.air_payload_hex) == new_payload);
    assert(b.controller.last_request_id_ == "waiting" && b.controller.last_tx_result_ == "queued");
    assert(b.controller.tx_success_count_ == 1); // Retry cannot confirm the new request.
    b.finish_worker(waiting);
    b.controller.consume_radio_runtime_events_();
    assert(b.controller.applied_request == "waiting" && b.controller.applied_light == 6);
    assert(b.controller.tx_success_count_ == 2 && b.controller.tx_failure_count_ == 1);
    assert(!b.controller.tx_in_progress_ && b.controller.rx_rmt_pulse_capture_enabled_);
    assert(b.controller.last_error_.empty() && b.controller.status_text_ == "ready");
    assert(b.controller.get_display_mode_badge_text() == "READY");
    // Only this newest successful original can arm another delayed burst.
    esphome::test_millis = 4000;
    b.controller.process_delayed_retransmission_();
    const auto new_retry = b.start_worker();
    assert(std::string(new_retry.request_id) == "waiting" && std::string(new_retry.air_payload_hex) == new_payload);
  }
  // Disabling optional retries does not discard an accepted user command.
  {
    Bench b;
    auto retry = b.start_retry(payload, bits);
    assert(b.send("waiting", new_payload, bits, 6));
    b.runtime.retry.set_enabled(false);
    b.finish_worker(retry);
    b.controller.consume_radio_runtime_events_();
    auto waiting = b.start_worker();
    assert(std::string(waiting.request_id) == "waiting");
    b.finish_worker(waiting);
    b.controller.consume_radio_runtime_events_();
    esphome::test_millis = 10000;
    b.controller.process_delayed_retransmission_();
    assert(b.commands.items.empty());
  }
  // Failed dispatch reports the accepted NEW request and restores listening.
  {
    Bench b;
    auto retry = b.start_retry(payload, bits);
    assert(b.send("waiting", payload, bits, 6));
    b.commands.reject_send = true;
    b.finish_worker(retry);
    b.controller.consume_radio_runtime_events_();
    assert(!b.runtime.deferred_tx_pending && !b.runtime.retry.pending());
    assert(b.controller.last_request_id_ == "waiting");
    assert(b.controller.last_tx_result_ == "error:deferred_tx_queue_full");
    assert(b.controller.tx_failure_count_ == 1 && b.controller.rx_rmt_pulse_capture_enabled_);
  }
  // Normal TX is not preempted or given an unbounded command backlog.
  {
    Bench b;
    assert(b.send("normal", payload, bits, 6));
    auto original = b.start_worker();
    assert(!b.send("overflow", payload, bits, 1));
    assert(!b.runtime.deferred_tx_pending);
    b.finish_worker(original);
    b.controller.consume_radio_runtime_events_();
    assert(b.controller.applied_request == "normal" && b.controller.applied_light == 6);
    assert(b.controller.last_request_id_ == "normal");
  }
  // Clearing an old fault must not hide a genuine initialization failure.
  {
    Bench b;
    b.controller.radio_runtime_init_state_ = RadioRuntimeInitState::FAILED;
    b.controller.last_tx_result_ = "ok";
    b.controller.refresh_status_text_();
    assert(b.controller.get_display_mode_badge_text() == "ERR");
  }
}
