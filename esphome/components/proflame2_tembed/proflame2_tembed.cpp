#include "proflame2_tembed.h"

#include <algorithm>
#include <cstring>
#include <inttypes.h>
#include <new>
#include <cstdio>
#include <cmath>

#include "freertos/queue.h"
#include "freertos/task.h"
#include "esphome/components/sensor/sensor.h"
#include "esphome/components/text_sensor/text_sensor.h"
#include "esphome/core/helpers.h"
#include "esphome/core/log.h"

namespace esphome {
namespace proflame2_tembed {

static const char* const TAG = "proflame2_tembed";
static constexpr const char* const PROFLAME_BUILD_MARKER = "native-groups-revert-check-20260430";

static constexpr uint32_t DISPLAY_BATTERY_POLL_INTERVAL_MS = 15000U;
static constexpr uint8_t DISPLAY_BATTERY_CLEAR_FAILURE_THRESHOLD = 3U;
static constexpr uint32_t RX_HEARTBEAT_LOG_INTERVAL_MS = 10000U;
static constexpr uint32_t RX_FIFO_ROLLING_EXPORT_WINDOW_MS = 6000U;
static constexpr uint32_t RX_FIFO_AUTO_COMPLETE_QUIET_MS = 350U;
static constexpr uint32_t RX_FIFO_AUTO_COMPLETE_MIN_INTERESTING_BYTES = 24U;
static constexpr uint32_t RX_FIFO_ACTIVE_LISTENER_MIN_ACTIVITY_BYTES = 24U;
static constexpr uint32_t RX_FIFO_ACTIVE_LISTENER_SCAN_INTERVAL_MS = 1500U;
static constexpr uint32_t RX_FIFO_ACCEPTED_DEDUP_MS = 5000U;

static std::string format_hex_byte_(uint8_t value) {
  char buffer[5];
  snprintf(buffer, sizeof(buffer), "0x%02X", value);
  return std::string(buffer);
}

static void append_hex_byte_(std::string& value, uint8_t byte) {
  static constexpr char HEX_DIGITS[] = "0123456789abcdef";
  value.push_back(HEX_DIGITS[(byte >> 4U) & 0x0FU]);
  value.push_back(HEX_DIGITS[byte & 0x0FU]);
}

static void append_hex24_(std::string& value, uint32_t data) {
  append_hex_byte_(value, static_cast<uint8_t>((data >> 16U) & 0xFFU));
  append_hex_byte_(value, static_cast<uint8_t>((data >> 8U) & 0xFFU));
  append_hex_byte_(value, static_cast<uint8_t>(data & 0xFFU));
}

static bool is_home_assistant_api_client_(const std::string& client_info) {
  return client_info.rfind("Home Assistant", 0) == 0;
}

static const char* tx_mode_to_string_(TXMode tx_mode) {
  switch (tx_mode) {
  case TXMode::CONTINUOUS_BURST:
    return "continuous_burst";
  case TXMode::REPEATED_STROBE:
    return "repeated_strobe";
  case TXMode::CLEAN_TIMING_TEST:
    return "clean_timing_test";
  case TXMode::PROFLAME_PWM_SYMBOLS:
    return "proflame_pwm_symbols";
  case TXMode::PROFLAME_NATIVE_GROUPS:
    return "proflame_native_groups";
  default:
    return "unknown";
  }
}

static const char* native_group_timing_profile_to_string_(NativeGroupTimingProfile profile) {
  switch (profile) {
  case NativeGroupTimingProfile::YARDSTICK_COMPAT:
    return "yardstick_compat";
  case NativeGroupTimingProfile::NATIVE_REMOTE:
    return "native_remote";
  default:
    return "unknown";
  }
}

static const char* native_group_repeat_boundary_mode_to_string_(NativeGroupRepeatBoundaryMode mode) {
  switch (mode) {
  case NativeGroupRepeatBoundaryMode::CONTINUOUS_TX:
    return "continuous_tx";
  case NativeGroupRepeatBoundaryMode::REENTER_TX:
    return "reenter_tx";
  default:
    return "unknown";
  }
}

static const char* runtime_start_reason_to_string_(RadioRuntimeStartReason reason) {
  switch (reason) {
  case RadioRuntimeStartReason::TX_REQUEST:
    return "tx_request";
  case RadioRuntimeStartReason::DIAGNOSTIC:
    return "diagnostic";
  default:
    return "unknown";
  }
}

static const char* runtime_state_to_string_(RadioRuntimeState state) {
  switch (state) {
  case RadioRuntimeState::IDLE:
    return "idle";
  case RadioRuntimeState::TX_ACTIVE:
    return "tx_active";
  case RadioRuntimeState::ERROR:
    return "error";
  default:
    return "unknown";
  }
}

static const char* test_pattern_mode_to_string_(TestPatternMode mode) {
  switch (mode) {
  case TestPatternMode::ALTERNATING_OOK:
    return "alternating_ook";
  case TestPatternMode::CARRIER_ON:
    return "carrier_on";
  case TestPatternMode::CARRIER_OFF:
    return "carrier_off";
  default:
    return "unknown";
  }
}

static const char* async_tx_data_pin_to_string_(AsyncTxDataPin pin) {
  switch (pin) {
  case AsyncTxDataPin::GDO0:
    return "gdo0";
  case AsyncTxDataPin::GDO2:
    return "gdo2";
  default:
    return "unknown";
  }
}

static std::string truncate_text_(const std::string& value, size_t max_len) {
  if (value.size() <= max_len) {
    return value;
  }
  if (max_len <= 3U) {
    return value.substr(0U, max_len);
  }
  return value.substr(0U, max_len - 3U) + "...";
}

static const char* runtime_init_state_to_string_(RadioRuntimeInitState state) {
  switch (state) {
  case RadioRuntimeInitState::UNINITIALIZED:
    return "uninitialized";
  case RadioRuntimeInitState::STARTING:
    return "starting";
  case RadioRuntimeInitState::READY:
    return "ready";
  case RadioRuntimeInitState::FAILED:
    return "failed";
  default:
    return "unknown";
  }
}

static int gpio_pin_number_(GPIOPin* pin) {
  if (pin == nullptr || !pin->is_internal()) {
    return -1;
  }
  return static_cast<int>(static_cast<InternalGPIOPin*>(pin)->get_pin());
}

static bool payload_bit_at_(const std::vector<uint8_t>& payload, uint32_t payload_bit_index) {
  const size_t byte_index = payload_bit_index / 8U;
  const int bit_index = 7 - static_cast<int>(payload_bit_index % 8U);
  return ((payload[byte_index] >> bit_index) & 0x01U) != 0;
}

static std::string decode_air_symbols_(const std::vector<uint8_t>& payload, uint32_t bit_length) {
  std::string symbols;
  if ((bit_length % 2U) != 0U) {
    return symbols;
  }
  symbols.reserve(bit_length / 2U);
  for (uint32_t bit_index = 0; bit_index < bit_length; bit_index += 2U) {
    const bool first = payload_bit_at_(payload, bit_index);
    const bool second = payload_bit_at_(payload, bit_index + 1U);
    if (first && second) {
      symbols.push_back('S');
    } else if (!first && second) {
      symbols.push_back('0');
    } else if (first && !second) {
      symbols.push_back('1');
    } else {
      symbols.push_back('Z');
    }
  }
  return symbols;
}

static std::string safe_substr_(const std::string& value, size_t pos, size_t count) {
  if (pos >= value.size()) {
    return "";
  }
  return value.substr(pos, std::min(count, value.size() - pos));
}

static void log_air_payload_symbols_(const std::string& request_id, const std::vector<uint8_t>& payload,
                                     uint32_t ha_payload_bit_length, uint32_t effective_payload_bit_length) {
  constexpr size_t PROFLAME_WORD_COUNT = 7;
  constexpr size_t SYMBOLS_PER_WORD = 13;
  constexpr size_t TRAILING_ZERO_SYMBOLS = 9;

  const uint32_t full_air_bit_length = static_cast<uint32_t>(payload.size() * 8U);
  const std::string effective_symbols = decode_air_symbols_(payload, effective_payload_bit_length);
  const std::string full_symbols = decode_air_symbols_(payload, full_air_bit_length);

  ESP_LOGI(TAG,
           "TX air payload semantics request_id=%s contract=manchester_nrz_levels "
           "symbol_map[S=11,0=01,1=10,Z=00] ha_bits=%" PRIu32 " effective_bits=%" PRIu32 " full_bits=%" PRIu32
           " effective_symbols=%u full_symbols=%u",
           request_id.c_str(), ha_payload_bit_length, effective_payload_bit_length, full_air_bit_length,
           static_cast<unsigned>(effective_symbols.size()), static_cast<unsigned>(full_symbols.size()));

  if (full_symbols.empty()) {
    ESP_LOGW(TAG, "TX air payload symbol decode failed request_id=%s full_bits=%" PRIu32, request_id.c_str(),
             full_air_bit_length);
    return;
  }

  ESP_LOGI(TAG, "TX air payload symbol stream request_id=%s effective=%s trailer=%s", request_id.c_str(),
           effective_symbols.c_str(),
           full_symbols.size() > effective_symbols.size() ? full_symbols.substr(effective_symbols.size()).c_str()
                                                          : "<none>");

  for (size_t word_index = 0; word_index < PROFLAME_WORD_COUNT; word_index++) {
    const size_t offset = word_index * SYMBOLS_PER_WORD;
    const std::string chunk = safe_substr_(full_symbols, offset, SYMBOLS_PER_WORD);
    const std::string data9 = chunk.size() >= 11 ? chunk.substr(2, 9) : "";
    const std::string parity = chunk.size() >= 12 ? chunk.substr(11, 1) : "";
    const std::string end_guard = chunk.size() >= 13 ? chunk.substr(12, 1) : "";
    ESP_LOGI(TAG, "TX air payload word[%u] request_id=%s chunk=%s sync=%s start=%s data9=%s parity=%s end=%s",
             static_cast<unsigned>(word_index), request_id.c_str(), chunk.c_str(),
             chunk.size() >= 1 ? chunk.substr(0, 1).c_str() : "?", chunk.size() >= 2 ? chunk.substr(1, 1).c_str() : "?",
             data9.empty() ? "?" : data9.c_str(), parity.empty() ? "?" : parity.c_str(),
             end_guard.empty() ? "?" : end_guard.c_str());
  }

  const std::string trailer = safe_substr_(full_symbols, PROFLAME_WORD_COUNT * SYMBOLS_PER_WORD, TRAILING_ZERO_SYMBOLS);
  ESP_LOGI(TAG, "TX air payload trailer request_id=%s trailer=%s", request_id.c_str(),
           trailer.empty() ? "<none>" : trailer.c_str());
}

template <size_t N> static void copy_string_(char (&dest)[N], const std::string& src) {
  if (N == 0) {
    return;
  }
  const size_t length = src.size() < (N - 1) ? src.size() : (N - 1);
  if (length > 0) {
    std::memcpy(dest, src.data(), length);
  }
  dest[length] = '\0';
}

template <size_t N> static std::string string_from_buffer_(const char (&src)[N]) {
  return std::string(src, strnlen(src, N));
}

enum class RadioRuntimeCommandKind : uint8_t {
  NONE = 0,
  TX = 1,
  TEST_PATTERN = 2,
};

enum class RadioRuntimeEventKind : uint8_t {
  NONE = 0,
  TX_COMPLETE = 1,
  TEST_PATTERN_COMPLETE = 2,
  RUNTIME_ERROR = 3,
};

static constexpr size_t RADIO_RUNTIME_MAX_REQUEST_ID = 80;
static constexpr size_t RADIO_RUNTIME_MAX_STATUS_TEXT = 80;
static constexpr size_t RADIO_RUNTIME_MAX_PAYLOAD_HEX = 160;
static constexpr size_t RADIO_RUNTIME_MAX_ERROR_TEXT = 128;
static constexpr size_t RADIO_RUNTIME_MAX_PAYLOAD_BYTES = 64;
static constexpr uint32_t RADIO_RUNTIME_TASK_STACK_BYTES = 24576;
#if PROFLAME2_TX_CLEAN_MODE
static constexpr UBaseType_t RADIO_RUNTIME_TASK_PRIORITY = configMAX_PRIORITIES - 2;
#if CONFIG_FREERTOS_UNICORE
static constexpr BaseType_t RADIO_RUNTIME_TASK_CORE = 0;
#else
static constexpr BaseType_t RADIO_RUNTIME_TASK_CORE = 1;
#endif
#else
static constexpr UBaseType_t RADIO_RUNTIME_TASK_PRIORITY = tskIDLE_PRIORITY + 1;
#endif

struct RadioRuntimeCommand {
  RadioRuntimeCommandKind kind{RadioRuntimeCommandKind::NONE};
  char request_id[RADIO_RUNTIME_MAX_REQUEST_ID]{};
  char air_payload_hex[RADIO_RUNTIME_MAX_PAYLOAD_HEX]{};
  char status_text[RADIO_RUNTIME_MAX_STATUS_TEXT]{};
  std::array<uint8_t, RADIO_RUNTIME_MAX_PAYLOAD_BYTES> payload{};
  uint32_t payload_length{0};
  uint32_t ha_payload_bit_length{0};
  uint32_t payload_bit_length{0};
  uint8_t repeat_count{0};
  uint32_t duration_ms{0};
  uint32_t period_us{0};
  TXMode tx_mode{TXMode::REPEATED_STROBE};
  NativeGroupTimingProfile native_group_timing_profile{NativeGroupTimingProfile::YARDSTICK_COMPAT};
  NativeGroupRepeatBoundaryMode native_group_repeat_boundary_mode{NativeGroupRepeatBoundaryMode::CONTINUOUS_TX};
  TestPatternMode test_pattern_mode{TestPatternMode::ALTERNATING_OOK};
  uint32_t inter_frame_gap_us{0};
  uint32_t post_frame_idle_gap_us{0};
  uint32_t pre_burst_low_us{0};
  uint32_t pre_frame_low_us{0};
};

struct RadioRuntimeEvent {
  RadioRuntimeEventKind kind{RadioRuntimeEventKind::NONE};
  bool ok{false};
  char request_id[RADIO_RUNTIME_MAX_REQUEST_ID]{};
  char air_payload_hex[RADIO_RUNTIME_MAX_PAYLOAD_HEX]{};
  uint32_t payload_length{0};
  uint32_t ha_payload_bit_length{0};
  uint32_t payload_bit_length{0};
  uint8_t repeat_count{0};
  uint32_t duration_ms{0};
  uint32_t elapsed_ms{0};
  uint8_t marcstate_before_tx{0};
  uint8_t marcstate_after_tx{0};
  uint8_t cc1101_partnum{0};
  uint8_t cc1101_version{0};
  TXMode tx_mode{TXMode::REPEATED_STROBE};
  NativeGroupTimingProfile native_group_timing_profile{NativeGroupTimingProfile::YARDSTICK_COMPAT};
  NativeGroupRepeatBoundaryMode native_group_repeat_boundary_mode{NativeGroupRepeatBoundaryMode::CONTINUOUS_TX};
  TXTimingDiagnostics timing{};
  char radio_error[RADIO_RUNTIME_MAX_ERROR_TEXT]{};
};

struct Proflame2TEmbedComponent::RadioRuntime {
  Proflame2TEmbedComponent* owner{nullptr};
  TaskHandle_t task_handle{nullptr};
  QueueHandle_t command_queue{nullptr};
  QueueHandle_t event_queue{nullptr};
  RadioRuntimeState state{RadioRuntimeState::IDLE};
  RadioRuntimeStartReason start_reason{RadioRuntimeStartReason::TX_REQUEST};
  bool worker_started{false};
  RadioRuntimeCommand command_buffer{};
  RadioRuntimeEvent event_buffer{};
};

static void radio_runtime_task_entry_(void* context) {
  auto* runtime = static_cast<Proflame2TEmbedComponent::RadioRuntime*>(context);
  if (runtime == nullptr || runtime->owner == nullptr) {
    vTaskDelete(nullptr);
    return;
  }

  runtime->worker_started = true;

  while (true) {
    if (xQueueReceive(runtime->command_queue, &runtime->command_buffer, portMAX_DELAY) != pdTRUE) {
      continue;
    }
    auto& command = runtime->command_buffer;
    runtime->state = RadioRuntimeState::TX_ACTIVE;

    auto& event = runtime->event_buffer;
    event = RadioRuntimeEvent{};
    event.kind = command.kind == RadioRuntimeCommandKind::TEST_PATTERN ? RadioRuntimeEventKind::TEST_PATTERN_COMPLETE
                                                                       : RadioRuntimeEventKind::TX_COMPLETE;
    copy_string_(event.request_id, std::string(command.request_id));
    copy_string_(event.air_payload_hex, std::string(command.air_payload_hex));
    event.payload_length = command.payload_length;
    event.ha_payload_bit_length = command.ha_payload_bit_length;
    event.payload_bit_length = command.payload_bit_length;
    event.repeat_count = command.repeat_count;
    event.duration_ms = command.duration_ms;
    event.tx_mode = command.tx_mode;
    event.native_group_timing_profile = command.native_group_timing_profile;

    std::string radio_error;
    if (!runtime->owner->is_radio_initialized()) {
      event.ok = false;
      copy_string_(event.radio_error, std::string("radio_not_initialized"));
    } else {
#if PROFLAME2_TEMBED_RADIO_RUNTIME_STUB
      event.ok = true;
      event.elapsed_ms = 0;
      runtime->state = RadioRuntimeState::IDLE;
#else
      event.cc1101_partnum = runtime->owner->read_partnum();
      event.cc1101_version = runtime->owner->read_version();
      event.marcstate_before_tx = runtime->owner->read_marcstate();
      if (command.kind == RadioRuntimeCommandKind::TX) {
        event.ok = runtime->owner->transmit_async_ook(
            command.payload.data(), command.payload_length, command.payload_bit_length, command.repeat_count,
            command.inter_frame_gap_us, command.tx_mode, command.native_group_timing_profile,
            command.native_group_repeat_boundary_mode, command.pre_burst_low_us, command.pre_frame_low_us,
            command.post_frame_idle_gap_us, event.elapsed_ms, event.timing, radio_error);
      } else if (command.kind == RadioRuntimeCommandKind::TEST_PATTERN) {
        event.ok = runtime->owner->transmit_test_pattern_async_ook(command.test_pattern_mode, command.duration_ms,
                                                                   command.period_us, event.elapsed_ms, event.timing,
                                                                   radio_error);
      } else {
        event.ok = false;
        radio_error = "unsupported_command";
      }
      event.marcstate_after_tx = runtime->owner->read_marcstate();
      if (!event.ok) {
        copy_string_(event.radio_error, radio_error.empty() ? std::string("radio_tx_failed") : radio_error);
        runtime->state = RadioRuntimeState::ERROR;
      } else {
        runtime->state = RadioRuntimeState::IDLE;
      }
#endif
    }

    if (xQueueSend(runtime->event_queue, &event, portMAX_DELAY) != pdTRUE) {
      runtime->state = RadioRuntimeState::ERROR;
    }
  }
}

void Proflame2TEmbedComponent::setup() {
  this->spi_setup();

  if (this->board_power_enable_pin_ != nullptr) {
    this->board_power_enable_pin_->setup();
    this->board_power_enable_pin_->digital_write(true);
  }
  if (this->rf_switch_sw1_pin_ != nullptr) {
    this->rf_switch_sw1_pin_->setup();
    this->rf_switch_sw1_pin_->digital_write(true);
  }
  if (this->rf_switch_sw0_pin_ != nullptr) {
    this->rf_switch_sw0_pin_->setup();
    this->rf_switch_sw0_pin_->digital_write(false);
  }
  if (this->cc1101_gdo0_pin_ != nullptr) {
    this->cc1101_gdo0_pin_->setup();
    this->cc1101_gdo0_pin_->digital_write(false);
  }
  if (this->cc1101_gdo2_pin_ != nullptr) {
    this->cc1101_gdo2_pin_->setup();
    this->cc1101_gdo2_pin_->digital_write(false);
  }

  std::string radio_error;
  if (!this->initialize_radio_(&radio_error)) {
    this->status_text_ = "fault";
    this->last_error_ = radio_error.empty() ? "radio_init_failed" : radio_error;
    this->last_tx_result_ = "error:radio_init_failed";
    ESP_LOGE(TAG, "Proflame2 T-Embed radio init failed: %s", this->last_error_.c_str());
    return;
  }

  this->last_error_.clear();
  this->cc1101_partnum_ = format_hex_byte_(this->read_partnum());
  this->cc1101_version_ = format_hex_byte_(this->read_version());
  this->display_.fireplace_state_label = "READY";
  this->display_.last_action_text = "none";
  this->display_.last_action_millis = millis();
  this->last_display_activity_ms_ = millis();
  this->display_backlight_refresh_pending_ = true;
  this->display_backlight_current_level_ = 1.0f;
  this->display_dim_deferred_ = false;
  this->battery_monitor_.setup(this->bus_);
  this->poll_battery_status_();
  this->update_display_from_telemetry_();
  this->mark_display_dirty_();
  ESP_LOGD(TAG, "Initial UI refresh requested");
  ESP_LOGCONFIG(TAG, "  Build marker: %s", PROFLAME_BUILD_MARKER);
  ESP_LOGCONFIG(TAG, "Proflame2 T-Embed TX skeleton ready");
  ESP_LOGCONFIG(TAG, "  TX frequency: %" PRIu32 " Hz", this->tx_frequency_hz_);
  ESP_LOGCONFIG(TAG, "  RX frequency: %" PRIu32 " Hz", this->rx_frequency_hz_);
  ESP_LOGCONFIG(TAG, "  data rate: %" PRIu32 " bps", this->data_rate_bps_);
  ESP_LOGCONFIG(TAG, "  TX repeat count: %u", this->tx_repeat_count_);
  ESP_LOGCONFIG(TAG, "  TX mode requested: %s", this->tx_mode_requested_.c_str());
  ESP_LOGCONFIG(TAG, "  TX mode resolved: %s", tx_mode_to_string_(this->tx_mode_));
  ESP_LOGCONFIG(TAG, "  native-group timing profile requested: %s",
                this->native_group_timing_profile_requested_.c_str());
  ESP_LOGCONFIG(TAG, "  native-group timing profile resolved: %s",
                native_group_timing_profile_to_string_(this->native_group_timing_profile_));
  ESP_LOGCONFIG(TAG, "  native-group repeat boundary mode requested: %s",
                this->native_group_repeat_boundary_mode_requested_.c_str());
  ESP_LOGCONFIG(TAG, "  native-group repeat boundary mode resolved: %s",
                native_group_repeat_boundary_mode_to_string_(this->native_group_repeat_boundary_mode_));
  ESP_LOGCONFIG(TAG, "  inter-frame gap: %" PRIu32 " us", this->inter_frame_gap_us_);
  ESP_LOGCONFIG(TAG, "  post-frame idle gap: %" PRIu32 " us", this->post_frame_idle_gap_us_);
  ESP_LOGCONFIG(TAG, "  pre-burst low: %" PRIu32 " us", this->pre_burst_low_us_);
  ESP_LOGCONFIG(TAG, "  pre-frame low: %" PRIu32 " us", this->pre_frame_low_us_);
  ESP_LOGCONFIG(TAG, "  diagnostic repeat override: %u", this->diagnostic_repeat_count_override_);
  ESP_LOGCONFIG(TAG, "  payload bit-length override: %" PRIu32, this->payload_bit_length_override_);
  ESP_LOGCONFIG(TAG, "  RX path: CC1101 FIFO semantic capture");
  ESP_LOGCONFIG(TAG, "  async TX data pin: %s", async_tx_data_pin_to_string_(this->async_tx_data_pin_));
  ESP_LOGCONFIG(TAG, "  CC1101 partnum/version: %s / %s", this->cc1101_partnum_.c_str(), this->cc1101_version_.c_str());
  ESP_LOGCONFIG(TAG, "  radio runtime: lazy start on first request");
  ESP_LOGCONFIG(TAG, "  radio runtime stub mode: %s", YESNO(PROFLAME2_TEMBED_RADIO_RUNTIME_STUB));
  this->refresh_status_text_();
  this->publish_telemetry_();
}

void Proflame2TEmbedComponent::loop() {
  this->process_pending_operation_();
  const uint32_t now = millis();
  if (this->display_dim_timeout_ms_ > 0U && !this->display_.display_dimmed &&
      (now - this->last_display_activity_ms_) >= this->display_dim_timeout_ms_) {
    if (this->is_display_update_allowed()) {
      this->set_display_dimmed_(true);
    } else {
      this->display_dim_deferred_ = true;
    }
  }
  if (this->display_dim_deferred_ && this->is_display_update_allowed()) {
    this->display_dim_deferred_ = false;
    this->set_display_dimmed_(true);
  }
  if (this->is_display_update_allowed()) {
    if (this->last_battery_poll_ms_ == 0U || (now - this->last_battery_poll_ms_) >= DISPLAY_BATTERY_POLL_INTERVAL_MS) {
      this->last_battery_poll_ms_ = now;
      this->poll_battery_status_();
    }
  }
  if (this->rx_fifo_capture_enabled_) {
    this->poll_rx_fifo_capture_();
#if PROFLAME2_TEMBED_DEBUG
    this->maybe_log_rx_fifo_capture_status_();
#endif
  }
  if (this->display_.active_operation && this->display_.active_operation_expires_millis > 0 &&
      millis() >= this->display_.active_operation_expires_millis) {
    this->display_.active_operation = false;
    this->display_.active_operation_expires_millis = 0;
    this->display_.active_operation_title.clear();
    this->display_.active_operation_detail.clear();
    this->mark_display_dirty_();
  }
  if (!this->tx_in_progress_) {
    if (this->is_display_update_allowed()) {
    }
    this->drain_deferred_debug_trace_();
  }
}

void Proflame2TEmbedComponent::set_capture_mode(const std::string& value) {
  const std::string normalized = value == "fifo_trailing_window" ? value : std::string("off");
  this->rx_active_listener_requested_ = false;
  this->rx_active_listener_filter_configured_ = false;
  this->rx_active_listener_profile_ = Proflame2DecodeProfile{};
  if (normalized == "fifo_trailing_window") {
    this->set_fifo_capture_enabled(true);
    return;
  }
  this->set_fifo_capture_enabled(false);
}

void Proflame2TEmbedComponent::configure_active_listener(bool enabled, uint32_t serial_id, uint8_t c1, uint8_t d1,
                                                         uint8_t c2, uint8_t d2) {
  if (!enabled) {
    const bool changed = this->rx_active_listener_requested_ || this->rx_active_listener_filter_configured_ ||
                         this->rx_active_listener_profile_.serial_id != 0U;
    this->rx_active_listener_requested_ = false;
    this->rx_active_listener_filter_configured_ = false;
    this->rx_active_listener_profile_ = Proflame2DecodeProfile{};
    if (changed) {
      ESP_LOGI(TAG, "RX active listener disabled");
    }
    this->set_fifo_capture_enabled(false);
    return;
  }

  const bool profile_valid =
      serial_id > 0U && serial_id <= 0xFFFFFFU && c1 <= 0x0FU && d1 <= 0x0FU && c2 <= 0x0FU && d2 <= 0x0FU;
  const bool changed = !this->rx_active_listener_requested_ ||
                       this->rx_active_listener_filter_configured_ != profile_valid ||
                       this->rx_active_listener_profile_.serial_id != (serial_id & 0xFFFFFFU) ||
                       this->rx_active_listener_profile_.c1 != c1 || this->rx_active_listener_profile_.d1 != d1 ||
                       this->rx_active_listener_profile_.c2 != c2 || this->rx_active_listener_profile_.d2 != d2;
  this->rx_active_listener_requested_ = true;
  this->rx_active_listener_filter_configured_ = profile_valid;
  this->rx_active_listener_profile_ = Proflame2DecodeProfile{profile_valid, serial_id, c1, d1, c2, d2};
  if (changed) {
    ESP_LOGI(TAG,
             "RX active listener enabled profile=%s serial_id=%06" PRIx32 " c1=%" PRIu8 " d1=%" PRIu8 " c2=%" PRIu8
             " d2=%" PRIu8,
             profile_valid ? "strict" : "raw_learning", static_cast<uint32_t>(serial_id & 0xFFFFFFU), c1, d1, c2, d2);
  }
  this->set_fifo_capture_enabled(true);
}

void Proflame2TEmbedComponent::set_fifo_capture_enabled(bool value) {
  if (this->rx_fifo_capture_enabled_ == value) {
    return;
  }
  this->rx_fifo_capture_enabled_ = value;
  if (value) {
    this->reset_rx_fifo_rolling_capture_(millis());
    std::string error;
    if (!this->is_radio_initialized() && !this->initialize_radio_(&error)) {
      this->rx_fifo_capture_enabled_ = false;
      this->rx_fifo_capture_configured_ = false;
      ESP_LOGW(TAG, "RX FIFO capture enable failed reason=%s", error.empty() ? "radio_init_failed" : error.c_str());
      return;
    }
    if (!this->configure_rx_fifo_capture_mode_(error)) {
      this->rx_fifo_capture_enabled_ = false;
      this->rx_fifo_capture_configured_ = false;
      ESP_LOGW(TAG, "RX FIFO capture enable failed reason=%s", error.empty() ? "fifo_config_failed" : error.c_str());
      return;
    }
    this->rx_fifo_capture_configured_ = true;
    this->rx_fifo_capture_last_stop_reason_ = "enabled";
    ESP_LOGI(TAG, "RX FIFO capture enabled mode=rolling_fifo_trailing_window export_window_ms=%" PRIu32,
             RX_FIFO_ROLLING_EXPORT_WINDOW_MS);
  } else {
    this->rx_fifo_capture_last_stop_reason_ = "disabled";
    this->rx_fifo_capture_configured_ = false;
    this->rx_fifo_paused_for_tx_ = false;
    this->strobe_(CC1101_SIDLE);
    this->strobe_(CC1101_SFRX);
    std::string error;
    this->apply_async_ook_registers_(error, false);
    ESP_LOGI(TAG, "RX FIFO capture disabled mode=rolling_fifo_trailing_window");
  }
  this->refresh_status_text_();
  this->publish_telemetry_();
}

#if PROFLAME2_TEMBED_DEBUG
void Proflame2TEmbedComponent::set_rx_fifo_profile(const std::string& value) {
  const std::string normalized =
      value == "rfcat_fixed_none_rfcat_defaults" || value == "rfcat_infinite_none_rfcat_defaults" ||
              value == "rfcat_fixed_none_rfcat_wide" || value == "rfcat_infinite_none_rfcat_wide" ||
              value == "rfcat_fixed_none" || value == "rfcat_infinite_none" || value == "rfcat_fixed_carrier" ||
              value == "rfcat_infinite_carrier"
          ? value
          : std::string("rfcat_fixed_none_rfcat_defaults");
  if (this->rx_fifo_profile_ == normalized) {
    return;
  }
  this->rx_fifo_profile_ = normalized;
  this->rx_fifo_capture_configured_ = false;
  if (this->rx_fifo_capture_enabled_) {
    this->reset_rx_fifo_rolling_capture_(millis());
  }
  ESP_LOGI(TAG, "RX FIFO profile set profile=%s", this->rx_fifo_profile_.c_str());
}

void Proflame2TEmbedComponent::complete_rx_fifo_capture() {
  if (!this->rx_fifo_capture_enabled_) {
    ESP_LOGW(TAG, "RX FIFO capture complete ignored reason=capture_not_enabled");
    return;
  }
  if (this->rx_fifo_capture_export_busy_) {
    ESP_LOGW(TAG, "RX FIFO capture complete ignored reason=export_busy");
    return;
  }
  this->finalize_rx_fifo_capture_("manual_complete");
}

void Proflame2TEmbedComponent::run_rx_fifo_probe() {
  const uint32_t probe_id = ++this->rx_fifo_probe_sequence_;
  constexpr uint32_t FIFO_PROBE_DURATION_MS = 3000U;
  ESP_LOGD(TAG,
           "RX fifo probe begin schema=1 probe_id=%" PRIu32
           " artifact_class=experimental_fifo_probe source=cc1101_rx_fifo"
           " frequency_hz=%" PRIu32 " data_rate_bps=%" PRIu32 " requested_duration_ms=%" PRIu32,
           probe_id, this->rx_frequency_hz_, this->data_rate_bps_, FIFO_PROBE_DURATION_MS);

  if (this->tx_in_progress_ || this->radio_runtime_state_ == RadioRuntimeState::TX_ACTIVE) {
    ESP_LOGW(TAG, "RX fifo probe end schema=1 probe_id=%" PRIu32 " ok=NO failure_reason=tx_active byte_count=0",
             probe_id);
    return;
  }

  std::string error;
  if (!this->is_radio_initialized() && !this->initialize_radio_(&error)) {
    ESP_LOGW(TAG, "RX fifo probe end schema=1 probe_id=%" PRIu32 " ok=NO failure_reason=%s byte_count=0", probe_id,
             error.empty() ? "radio_init_failed" : error.c_str());
    return;
  }

  RXFifoProbeResult result;
  const bool ok =
      this->rx_fifo_probe(this->rx_frequency_hz_, this->data_rate_bps_, FIFO_PROBE_DURATION_MS, result, error);

  ESP_LOGD(TAG,
           "RX fifo probe meta settings schema=1 probe_id=%" PRIu32 " mdmcfg4=0x%02X mdmcfg3=0x%02X mdmcfg2=0x%02X"
           " pktctrl1=0x%02X pktctrl0=0x%02X sync1=0x%02X sync0=0x%02X",
           probe_id, result.mdmcfg4, result.mdmcfg3, result.mdmcfg2, result.pktctrl1, result.pktctrl0, result.sync1,
           result.sync0);
  ESP_LOGD(TAG, "RX fifo probe meta agc schema=1 probe_id=%" PRIu32 " agcctrl2=0x%02X agcctrl1=0x%02X agcctrl0=0x%02X",
           probe_id, result.agcctrl2, result.agcctrl1, result.agcctrl0);
  ESP_LOGD(TAG,
           "RX fifo probe meta status schema=1 probe_id=%" PRIu32
           " ok=%s byte_count=%u buffer_full=%s rx_fifo_overflow=%s"
           " rxbytes_max=%u rxbytes_final=%u poll_count=%" PRIu32,
           probe_id, YESNO(ok), result.byte_count, YESNO(result.buffer_full), YESNO(result.rx_fifo_overflow),
           result.rxbytes_max, result.rxbytes_final, result.poll_count);
  ESP_LOGD(TAG,
           "RX fifo probe meta radio schema=1 probe_id=%" PRIu32
           " partnum=0x%02X version=0x%02X marcstate_before=0x%02X"
           " marcstate_after_config=0x%02X marcstate_after_rx=0x%02X"
           " marcstate_after_idle=0x%02X rssi_raw=0x%02X lqi_raw=0x%02X pktstatus=0x%02X",
           probe_id, result.partnum, result.version, result.marcstate_before, result.marcstate_after_config,
           result.marcstate_after_rx, result.marcstate_after_idle, result.rssi_raw, result.lqi_raw, result.pktstatus);
  ESP_LOGD(TAG,
           "RX fifo probe meta timing schema=1 probe_id=%" PRIu32 " started_tick_ms=%" PRIu32
           " completed_tick_ms=%" PRIu32 " elapsed_ms=%" PRIu32,
           probe_id, result.started_tick_ms, result.completed_tick_ms, result.elapsed_ms);

  constexpr size_t FIFO_CHUNK_BYTES = 24U;
  for (size_t start = 0U; start < result.byte_count; start += FIFO_CHUNK_BYTES) {
    const size_t count = std::min(FIFO_CHUNK_BYTES, static_cast<size_t>(result.byte_count) - start);
    std::string hex;
    hex.reserve(count * 2U);
    for (size_t offset = 0U; offset < count; offset++) {
      char byte_hex[3];
      snprintf(byte_hex, sizeof(byte_hex), "%02X", result.bytes[start + offset]);
      hex += byte_hex;
    }
    ESP_LOGD(TAG, "RX fifo probe chunk schema=1 probe_id=%" PRIu32 " chunk=%u offset=%u count=%u hex=%s", probe_id,
             static_cast<unsigned>(start / FIFO_CHUNK_BYTES), static_cast<unsigned>(start),
             static_cast<unsigned>(count), hex.c_str());
  }
  ESP_LOGD(TAG,
           "RX fifo probe end schema=1 probe_id=%" PRIu32
           " ok=%s failure_reason=%s byte_count=%u buffer_full=%s rx_fifo_overflow=%s",
           probe_id, YESNO(ok), ok ? "none" : (error.empty() ? "unknown" : error.c_str()), result.byte_count,
           YESNO(result.buffer_full), YESNO(result.rx_fifo_overflow));
}
#endif

void Proflame2TEmbedComponent::dump_config() {
  ESP_LOGCONFIG(TAG, "Proflame2 T-Embed CC1101 endpoint");
  ESP_LOGCONFIG(TAG, "  mode: TX with FIFO learning and active listening");
  ESP_LOGCONFIG(TAG, "  status: %s", this->status_text_.c_str());
  ESP_LOGCONFIG(TAG, "  RX FIFO profile: %s", this->rx_fifo_profile_name_());
  ESP_LOGCONFIG(TAG, "  RX FIFO capture enabled: %s", YESNO(this->rx_fifo_capture_enabled_));
  ESP_LOGCONFIG(TAG, "  TX path: cc1101_async_gdo0_msb_first");
  ESP_LOGCONFIG(TAG, "  TX mode requested: %s", this->tx_mode_requested_.c_str());
  ESP_LOGCONFIG(TAG, "  TX mode resolved: %s", tx_mode_to_string_(this->tx_mode_));
  ESP_LOGCONFIG(TAG, "  native-group timing profile requested: %s",
                this->native_group_timing_profile_requested_.c_str());
  ESP_LOGCONFIG(TAG, "  native-group timing profile resolved: %s",
                native_group_timing_profile_to_string_(this->native_group_timing_profile_));
  ESP_LOGCONFIG(TAG, "  native-group repeat boundary mode requested: %s",
                this->native_group_repeat_boundary_mode_requested_.c_str());
  ESP_LOGCONFIG(TAG, "  native-group repeat boundary mode resolved: %s",
                native_group_repeat_boundary_mode_to_string_(this->native_group_repeat_boundary_mode_));
  ESP_LOGCONFIG(TAG, "  inter-frame gap: %" PRIu32 " us", this->inter_frame_gap_us_);
  ESP_LOGCONFIG(TAG, "  post-frame idle gap: %" PRIu32 " us", this->post_frame_idle_gap_us_);
  ESP_LOGCONFIG(TAG, "  pre-burst low: %" PRIu32 " us", this->pre_burst_low_us_);
  ESP_LOGCONFIG(TAG, "  pre-frame low: %" PRIu32 " us", this->pre_frame_low_us_);
  ESP_LOGCONFIG(TAG, "  diagnostic repeat override: %u", this->diagnostic_repeat_count_override_);
  ESP_LOGCONFIG(TAG, "  payload bit-length override: %" PRIu32, this->payload_bit_length_override_);
}

bool Proflame2TEmbedComponent::tx_debug_logging_enabled_() const {
  return this->display_.display_debug_mode;
}

const char* Proflame2TEmbedComponent::rx_fifo_profile_name_() const {
#if PROFLAME2_TEMBED_DEBUG
  return this->rx_fifo_profile_.c_str();
#else
  return "rfcat_fixed_none_rfcat_wide";
#endif
}

bool Proflame2TEmbedComponent::configure_rx_fifo_capture_mode_(std::string& error) {
  uint8_t mdmcfg4 = 0;
  uint8_t mdmcfg3 = 0;
  this->compute_drate_registers_(this->data_rate_bps_, mdmcfg4, mdmcfg3);
  const uint32_t frequency_word = this->compute_frequency_word_(this->rx_frequency_hz_);
#if PROFLAME2_TEMBED_DEBUG
  const bool infinite_mode = this->rx_fifo_profile_ == "rfcat_infinite_none" ||
                             this->rx_fifo_profile_ == "rfcat_infinite_carrier" ||
                             this->rx_fifo_profile_ == "rfcat_infinite_none_rfcat_defaults" ||
                             this->rx_fifo_profile_ == "rfcat_infinite_none_rfcat_wide";
  const bool carrier_gated =
      this->rx_fifo_profile_ == "rfcat_fixed_carrier" || this->rx_fifo_profile_ == "rfcat_infinite_carrier";
  const bool rfcat_defaults = this->rx_fifo_profile_ == "rfcat_fixed_none_rfcat_defaults" ||
                              this->rx_fifo_profile_ == "rfcat_infinite_none_rfcat_defaults" ||
                              this->rx_fifo_profile_ == "rfcat_fixed_none_rfcat_wide" ||
                              this->rx_fifo_profile_ == "rfcat_infinite_none_rfcat_wide";
  const bool wide_bandwidth = this->rx_fifo_profile_ == "rfcat_fixed_none_rfcat_wide" ||
                              this->rx_fifo_profile_ == "rfcat_infinite_none_rfcat_wide";
#else
  constexpr bool infinite_mode = false;
  constexpr bool carrier_gated = false;
  constexpr bool rfcat_defaults = true;
  constexpr bool wide_bandwidth = true;
#endif
  const uint8_t sync_mode = carrier_gated ? 0x04 : 0x00;
  const uint8_t pktctrl0 = infinite_mode ? 0x02 : 0x00;
  const uint8_t mdmcfg4_effective = wide_bandwidth ? static_cast<uint8_t>((mdmcfg4 & 0x0F) | 0x50) : mdmcfg4;
  const uint8_t mcsm1 = rfcat_defaults ? 0x3F : 0x30;
  const uint8_t foccfg = rfcat_defaults ? 0x17 : 0x16;
  const uint8_t agcctrl2 = rfcat_defaults ? 0x03 : 0x43;
  const uint8_t frend1 = wide_bandwidth ? 0xB6 : 0x56;
  const uint8_t frend0 = rfcat_defaults ? 0x10 : 0x11;
  const uint8_t test2 = wide_bandwidth ? 0x88 : 0x81;
  const uint8_t test1 = wide_bandwidth ? 0x31 : 0x35;

  this->strobe_(CC1101_SIDLE);
  this->strobe_(CC1101_SFRX);
  this->strobe_(CC1101_SFTX);
  this->write_register_(CC1101_IOCFG2, 0x2E);
  this->write_register_(CC1101_IOCFG1, 0x2E);
  this->write_register_(CC1101_IOCFG0, 0x2E);
  this->write_register_(CC1101_FIFOTHR, 0x47);
  this->write_register_(CC1101_SYNC1, 0x00);
  this->write_register_(CC1101_SYNC0, 0x00);
  this->write_register_(CC1101_PKTLEN, 0xFF);
  this->write_register_(CC1101_PKTCTRL1, 0x00);
  this->write_register_(CC1101_PKTCTRL0, pktctrl0);
  this->write_register_(CC1101_FSCTRL1, 0x06);
  this->write_register_(CC1101_FSCTRL0, 0x00);
  this->write_register_(CC1101_FREQ2, static_cast<uint8_t>((frequency_word >> 16) & 0xFF));
  this->write_register_(CC1101_FREQ1, static_cast<uint8_t>((frequency_word >> 8) & 0xFF));
  this->write_register_(CC1101_FREQ0, static_cast<uint8_t>(frequency_word & 0xFF));
  this->write_register_(CC1101_MDMCFG4, mdmcfg4_effective);
  this->write_register_(CC1101_MDMCFG3, mdmcfg3);
  this->write_register_(CC1101_MDMCFG2, static_cast<uint8_t>(0x30 | sync_mode));
  this->write_register_(CC1101_MDMCFG1, 0x02);
  this->write_register_(CC1101_MDMCFG0, 0xF8);
  this->write_register_(CC1101_DEVIATN, 0x00);
  this->write_register_(CC1101_MCSM1, mcsm1);
  this->write_register_(CC1101_MCSM0, 0x18);
  this->write_register_(CC1101_FOCCFG, foccfg);
  this->write_register_(CC1101_BSCFG, 0x6C);
  this->write_register_(CC1101_AGCCTRL2, agcctrl2);
  this->write_register_(CC1101_AGCCTRL1, 0x40);
  this->write_register_(CC1101_AGCCTRL0, 0x91);
  this->write_register_(CC1101_FREND1, frend1);
  this->write_register_(CC1101_FREND0, frend0);
  this->write_register_(CC1101_FSCAL3, 0xE9);
  this->write_register_(CC1101_FSCAL2, 0x2A);
  this->write_register_(CC1101_FSCAL1, 0x00);
  this->write_register_(CC1101_FSCAL0, 0x1F);
  this->write_register_(CC1101_TEST2, test2);
  this->write_register_(CC1101_TEST1, test1);
  this->write_register_(CC1101_TEST0, 0x09);
  this->strobe_(CC1101_SCAL);
  delay(1);
  this->strobe_(CC1101_SFRX);
  this->strobe_(CC1101_SRX);
  delay(1);
  this->rx_fifo_.set_radio_status(this->read_marcstate(), this->rx_fifo_.rssi_raw_last(), this->rx_fifo_.lqi_raw_last(),
                                  this->rx_fifo_.pktstatus_last());
  ESP_LOGI(TAG,
           "RX FIFO configured profile=%s pktctrl0=0x%02X mdmcfg4=0x%02X mdmcfg2=0x%02X mdmcfg1=0x%02X agcctrl2=0x%02X "
           "frend0=0x%02X",
           this->rx_fifo_profile_name_(), this->read_register_(CC1101_PKTCTRL0), this->read_register_(CC1101_MDMCFG4),
           this->read_register_(CC1101_MDMCFG2), this->read_register_(CC1101_MDMCFG1),
           this->read_register_(CC1101_AGCCTRL2), this->read_register_(CC1101_FREND0));
  error.clear();
  return true;
}

void Proflame2TEmbedComponent::reset_rx_fifo_rolling_capture_(uint32_t enable_tick_ms) {
  this->rx_fifo_capture_export_busy_ = false;
  this->rx_fifo_.reset(enable_tick_ms);
}

void Proflame2TEmbedComponent::record_rx_fifo_byte_(uint8_t value, uint32_t tick_ms) {
  this->rx_fifo_.record_byte(value, tick_ms);
}

void Proflame2TEmbedComponent::poll_rx_fifo_capture_() {
  if (!this->rx_fifo_capture_enabled_ || this->rx_fifo_capture_export_busy_) {
    return;
  }
  if (this->tx_in_progress_ || this->radio_runtime_state_ == RadioRuntimeState::TX_ACTIVE) {
    return;
  }
  if (!this->rx_fifo_capture_configured_) {
    std::string error;
    this->rx_fifo_capture_configured_ = this->configure_rx_fifo_capture_mode_(error);
    if (!this->rx_fifo_capture_configured_) {
      ESP_LOGW(TAG, "RX FIFO poll config failed reason=%s", error.empty() ? "fifo_config_failed" : error.c_str());
      return;
    }
  }
  this->rx_fifo_.increment_poll_count();
  const uint8_t rxbytes_status = this->read_status_register_(CC1101_RXBYTES);
  uint8_t fifo_count = static_cast<uint8_t>(rxbytes_status & 0x7FU);
  this->rx_fifo_.update_rxbytes_max(fifo_count);
  if ((rxbytes_status & 0x80U) != 0U) {
    this->rx_fifo_.mark_hardware_overflow();
    this->strobe_(CC1101_SIDLE);
    this->strobe_(CC1101_SFRX);
    this->strobe_(CC1101_SRX);
    return;
  }
  uint8_t* const scratch = this->rx_fifo_.drain_scratch_data();
  const uint8_t scratch_size = static_cast<uint8_t>(this->rx_fifo_.drain_scratch_size());
  while (fifo_count > 0U) {
    const uint8_t drain_count = std::min<uint8_t>(fifo_count, scratch_size);
    this->read_burst_register_(CC1101_RXFIFO, scratch, drain_count);
    const uint32_t tick_ms = millis();
    for (uint8_t index = 0U; index < drain_count; index++) {
      this->record_rx_fifo_byte_(scratch[index], tick_ms);
    }
    fifo_count = static_cast<uint8_t>(this->read_status_register_(CC1101_RXBYTES) & 0x7FU);
    this->rx_fifo_.update_rxbytes_max(fifo_count);
  }
  this->rx_fifo_.set_rxbytes_final(static_cast<uint8_t>(this->read_status_register_(CC1101_RXBYTES) & 0x7FU));
  this->rx_fifo_.set_radio_status(this->read_marcstate(), this->read_status_register_(CC1101_RSSI),
                                  this->read_status_register_(CC1101_LQI),
                                  this->read_status_register_(CC1101_PKTSTATUS));
  if (this->rx_fifo_.marcstate_last() != 0x0DU) {
    this->strobe_(CC1101_SRX);
  }
  this->maybe_auto_complete_rx_fifo_capture_();
  this->maybe_log_rx_fifo_capture_status_();
}

void Proflame2TEmbedComponent::maybe_auto_complete_rx_fifo_capture_() {
  if (!this->rx_fifo_capture_enabled_ || this->rx_fifo_capture_export_busy_ ||
      this->rx_fifo_capture_finalize_in_progress_) {
    return;
  }
  const bool strict_active_listener = this->rx_active_listener_filter_configured_;
  const uint32_t activity_bytes = strict_active_listener ? this->rx_fifo_.bytes_seen_since_enable()
                                                         : this->rx_fifo_.interesting_bytes_since_export();
  const uint32_t last_activity_tick =
      strict_active_listener ? this->rx_fifo_.last_byte_tick_ms() : this->rx_fifo_.last_interesting_byte_tick_ms();
  const uint32_t min_activity_bytes =
      strict_active_listener ? RX_FIFO_ACTIVE_LISTENER_MIN_ACTIVITY_BYTES : RX_FIFO_AUTO_COMPLETE_MIN_INTERESTING_BYTES;
  if (activity_bytes < min_activity_bytes || last_activity_tick == 0U) {
    return;
  }
  const uint32_t now_ms = millis();
  if (strict_active_listener) {
    if (this->rx_fifo_.capture_enable_tick_ms() == 0U ||
        now_ms - this->rx_fifo_.capture_enable_tick_ms() < RX_FIFO_ROLLING_EXPORT_WINDOW_MS) {
      return;
    }
    if (this->rx_fifo_.active_listener_last_scan_ms() != 0U &&
        now_ms - this->rx_fifo_.active_listener_last_scan_ms() < RX_FIFO_ACTIVE_LISTENER_SCAN_INTERVAL_MS) {
      return;
    }
    this->rx_fifo_.set_active_listener_last_scan(now_ms);
    this->finalize_rx_fifo_capture_("active_listener_periodic_scan");
    return;
  }
  if (now_ms - last_activity_tick < RX_FIFO_AUTO_COMPLETE_QUIET_MS) {
    return;
  }
  this->finalize_rx_fifo_capture_("auto_quiet_after_activity");
}

void Proflame2TEmbedComponent::finalize_rx_fifo_capture_(const char* reason) {
  if (this->rx_fifo_capture_finalize_in_progress_) {
    return;
  }
  this->rx_fifo_capture_finalize_in_progress_ = true;
  this->poll_rx_fifo_capture_();
  this->rx_fifo_capture_export_busy_ = true;
  this->rx_fifo_.set_complete_tick(millis());
  this->rx_fifo_capture_session_index_++;
  const bool report_scan_complete = this->dump_rx_fifo_rolling_capture_(reason);
  this->rx_fifo_capture_export_busy_ = false;
  this->rx_fifo_capture_last_stop_reason_ = reason;
  if (this->rx_active_listener_filter_configured_) {
    if (report_scan_complete) {
      ESP_LOGI(TAG,
               "RX FIFO active listener scan complete session=%" PRIu32
               " reason=%s capture_resume_after_export=YES enabled=%s",
               this->rx_fifo_capture_session_index_, reason, YESNO(this->rx_fifo_capture_enabled_));
    }
    this->rx_fifo_capture_finalize_in_progress_ = false;
    return;
  }
  this->reset_rx_fifo_rolling_capture_(millis());
  std::string error;
  if (this->rx_fifo_capture_enabled_ && this->configure_rx_fifo_capture_mode_(error)) {
    this->rx_fifo_capture_configured_ = true;
  } else if (this->rx_fifo_capture_enabled_) {
    this->rx_fifo_capture_configured_ = false;
    ESP_LOGW(TAG, "RX FIFO capture resume failed reason=%s", error.empty() ? "fifo_config_failed" : error.c_str());
  }
  ESP_LOGI(TAG,
           "RX FIFO capture complete exported session=%" PRIu32 " reason=%s capture_resume_after_export=%s enabled=%s",
           this->rx_fifo_capture_session_index_, reason, YESNO(this->rx_fifo_capture_configured_),
           YESNO(this->rx_fifo_capture_enabled_));
  this->rx_fifo_capture_finalize_in_progress_ = false;
}

bool Proflame2TEmbedComponent::dump_rx_fifo_rolling_capture_(const char* reason) {
  const uint32_t export_id = ++this->rx_fifo_probe_sequence_;
  const FifoRxWindow window = this->rx_fifo_.select_window(millis(), RX_FIFO_ROLLING_EXPORT_WINDOW_MS);
  const uint32_t complete_ms = window.complete_tick_ms;
  const uint32_t window_start_ms = window.window_start_tick_ms;
  const uint16_t selected_count = window.selected_count;
  const uint8_t* const selected_bytes = this->rx_fifo_.selected_data();
  const bool strict_active_listener = this->rx_active_listener_filter_configured_;

#if PROFLAME2_TEMBED_DEBUG
  ESP_LOGD(TAG,
           "RX fifo probe begin schema=2 probe_id=%" PRIu32
           " artifact_class=experimental_fifo_probe source=cc1101_rx_fifo"
           " capture_mode=rolling_fifo_trailing_window profile=%s frequency_hz=%" PRIu32 " data_rate_bps=%" PRIu32
           " requested_duration_ms=%" PRIu32,
           export_id, this->rx_fifo_profile_name_(), this->rx_frequency_hz_, this->data_rate_bps_,
           RX_FIFO_ROLLING_EXPORT_WINDOW_MS);
#endif

#if PROFLAME2_TEMBED_DEBUG
  constexpr size_t FIFO_EXPORT_CHUNK_BYTES = 24U;
  std::string hex;
  uint16_t chunk_index = 0U;
  uint16_t chunk_offset = 0U;
#endif
  std::string event_payload_hex;
  if (!strict_active_listener) {
    event_payload_hex.reserve(static_cast<size_t>(selected_count) * 2U);
  }
  for (uint16_t index = 0U; index < selected_count; index++) {
    char byte_hex[3];
    snprintf(byte_hex, sizeof(byte_hex), "%02X", selected_bytes[index]);
#if PROFLAME2_TEMBED_DEBUG
    hex += byte_hex;
#endif
    if (!strict_active_listener) {
      event_payload_hex += byte_hex;
    }
#if PROFLAME2_TEMBED_DEBUG
    const uint16_t emitted_count = static_cast<uint16_t>(index + 1U);
    if ((emitted_count % FIFO_EXPORT_CHUNK_BYTES) == 0U) {
      ESP_LOGD(TAG, "RX fifo probe chunk schema=2 probe_id=%" PRIu32 " chunk=%u offset=%u count=%u hex=%s", export_id,
               chunk_index++, chunk_offset, static_cast<unsigned>(FIFO_EXPORT_CHUNK_BYTES), hex.c_str());
      chunk_offset = emitted_count;
      hex.clear();
    }
#endif
  }
#if PROFLAME2_TEMBED_DEBUG
  if (!hex.empty()) {
    ESP_LOGD(TAG, "RX fifo probe chunk schema=2 probe_id=%" PRIu32 " chunk=%u offset=%u count=%u hex=%s", export_id,
             chunk_index, chunk_offset, static_cast<unsigned>(selected_count - chunk_offset), hex.c_str());
  }
#endif

  const bool trailing_window_complete = window.trailing_window_complete;
  const bool dropped_required_window_byte = window.dropped_required_window_byte;
  const uint32_t post_last_byte_quiet_ms = window.post_last_byte_quiet_ms;
  const uint32_t first_byte_delta_from_window_ms = window.first_byte_delta_from_window_start_ms;
  const uint32_t wall_clock_window_coverage_ms = window.wall_clock_window_coverage_ms;

  if (strict_active_listener) {
    const ActiveListenerOutcome outcome = this->active_listener_.evaluate_window(
        selected_bytes, selected_count, trailing_window_complete, this->rx_active_listener_profile_, complete_ms,
        RX_FIFO_ACCEPTED_DEDUP_MS);
    switch (outcome.type) {
    case ActiveListenerOutcomeType::IDLE_NO_CANDIDATE:
      this->record_rx_idle_noise_(outcome.reason);
      return false;
    case ActiveListenerOutcomeType::DROPPED:
      this->record_rx_dropped_packet_(outcome.stage, outcome.reason, &outcome.decoded, selected_bytes, selected_count,
                                      complete_ms, post_last_byte_quiet_ms);
      return true;
    case ActiveListenerOutcomeType::DUPLICATE:
      ESP_LOGD(TAG,
               "RX active listener duplicate suppressed serial=%06" PRIx32
               " cmd1=%02x cmd2=%02x err1=%02x err2=%02x age_ms=%" PRIu32,
               static_cast<uint32_t>(outcome.decoded.serial_id & 0xFFFFFFU), outcome.decoded.cmd1, outcome.decoded.cmd2,
               outcome.decoded.err1, outcome.decoded.err2, outcome.duplicate_age_ms);
      return true;
    case ActiveListenerOutcomeType::ACCEPTED:
      this->publish_decoded_rx_packet_(export_id, outcome.decoded, selected_bytes, selected_count, complete_ms,
                                       post_last_byte_quiet_ms, reason);
      return true;
    }
  }

#if PROFLAME2_TEMBED_DEBUG
  ESP_LOGD(TAG,
           "RX fifo probe meta window schema=2 probe_id=%" PRIu32 " export_window_ms=%" PRIu32
           " export_window_start_tick_ms=%" PRIu32 " export_window_end_tick_ms=%" PRIu32
           " wall_clock_window_coverage_ms=%" PRIu32,
           export_id, RX_FIFO_ROLLING_EXPORT_WINDOW_MS, window_start_ms, complete_ms, wall_clock_window_coverage_ms);
  ESP_LOGD(TAG,
           "RX fifo probe meta status schema=2 probe_id=%" PRIu32
           " ok=YES byte_count=%u buffer_full=%s rx_fifo_overflow=%s"
           " rolling_history_overflow=%s dropped_required_window_byte=%s"
           " trailing_window_complete=%s insufficient_trailing_window=%s",
           export_id, selected_count, YESNO(false), YESNO(this->rx_fifo_.hardware_overflow()),
           YESNO(this->rx_fifo_.rolling_overflow()), YESNO(dropped_required_window_byte),
           YESNO(trailing_window_complete), YESNO(!trailing_window_complete));
  ESP_LOGD(TAG,
           "RX fifo probe meta timing schema=2 probe_id=%" PRIu32 " capture_enable_tick_ms=%" PRIu32
           " capture_complete_tick_ms=%" PRIu32 " first_byte_delta_from_window_start_ms=%" PRIu32
           " post_last_byte_quiet_ms=%" PRIu32 " elapsed_ms=%" PRIu32,
           export_id, this->rx_fifo_.capture_enable_tick_ms(), complete_ms, first_byte_delta_from_window_ms,
           post_last_byte_quiet_ms,
           complete_ms >= this->rx_fifo_.capture_enable_tick_ms()
               ? complete_ms - this->rx_fifo_.capture_enable_tick_ms()
               : 0U);
  ESP_LOGD(TAG,
           "RX fifo probe meta radio_status schema=2 probe_id=%" PRIu32
           " marcstate_after_rx=0x%02X rssi_raw=0x%02X lqi_raw=0x%02X pktstatus=0x%02X"
           " rxbytes_max=%u rxbytes_final=%u poll_count=%" PRIu32,
           export_id, this->rx_fifo_.marcstate_last(), this->rx_fifo_.rssi_raw_last(), this->rx_fifo_.lqi_raw_last(),
           this->rx_fifo_.pktstatus_last(), this->rx_fifo_.rxbytes_max(), this->rx_fifo_.rxbytes_final(),
           this->rx_fifo_.poll_count());
  ESP_LOGD(TAG,
           "RX fifo probe meta radio_regs1 schema=2 probe_id=%" PRIu32
           " pktctrl0=0x%02X pktctrl1=0x%02X mdmcfg4=0x%02X mdmcfg3=0x%02X"
           " mdmcfg2=0x%02X mdmcfg1=0x%02X mdmcfg0=0x%02X",
           export_id, this->read_register_(CC1101_PKTCTRL0), this->read_register_(CC1101_PKTCTRL1),
           this->read_register_(CC1101_MDMCFG4), this->read_register_(CC1101_MDMCFG3),
           this->read_register_(CC1101_MDMCFG2), this->read_register_(CC1101_MDMCFG1),
           this->read_register_(CC1101_MDMCFG0));
  ESP_LOGD(TAG,
           "RX fifo probe meta radio_regs2 schema=2 probe_id=%" PRIu32
           " agcctrl2=0x%02X agcctrl1=0x%02X agcctrl0=0x%02X"
           " frend1=0x%02X frend0=0x%02X bscfg=0x%02X foccfg=0x%02X test2=0x%02X test1=0x%02X",
           export_id, this->read_register_(CC1101_AGCCTRL2), this->read_register_(CC1101_AGCCTRL1),
           this->read_register_(CC1101_AGCCTRL0), this->read_register_(CC1101_FREND1),
           this->read_register_(CC1101_FREND0), this->read_register_(CC1101_BSCFG), this->read_register_(CC1101_FOCCFG),
           this->read_register_(CC1101_TEST2), this->read_register_(CC1101_TEST1));
  ESP_LOGD(TAG,
           "RX fifo probe end schema=2 probe_id=%" PRIu32
           " ok=YES failure_reason=none byte_count=%u buffer_full=NO rx_fifo_overflow=%s",
           export_id, selected_count, YESNO(this->rx_fifo_.hardware_overflow()));
#endif
  this->fire_homeassistant_event("esphome.proflame2_rx_packet",
                                 {
                                     {"schema_version", "2"},
                                     {"protocol", "proflame2"},
                                     {"event_kind", "fifo_capture"},
                                     {"artifact_class", "raw_fifo_window"},
                                     {"source", "lilygo_cc1101_fifo"},
                                     {"payload_hex", event_payload_hex},
                                     {"packet_count", std::to_string(export_id)},
                                     {"freq_hz", std::to_string(this->rx_frequency_hz_)},
                                     {"device_tick_ms", std::to_string(complete_ms)},
                                     {"rssi", std::to_string(static_cast<unsigned>(this->rx_fifo_.rssi_raw_last()))},
                                     {"lqi", std::to_string(static_cast<unsigned>(this->rx_fifo_.lqi_raw_last()))},
                                     {"capture_mode", "rolling_fifo_trailing_window"},
                                     {"profile", this->rx_fifo_profile_name_()},
                                     {"stop_reason", reason},
                                     {"byte_count", std::to_string(selected_count)},
                                     {"trailing_window_complete", YESNO(trailing_window_complete)},
                                     {"insufficient_trailing_window", YESNO(!trailing_window_complete)},
                                     {"rx_fifo_overflow", YESNO(this->rx_fifo_.hardware_overflow())},
                                     {"rolling_history_overflow", YESNO(this->rx_fifo_.rolling_overflow())},
                                     {"dropped_required_window_byte", YESNO(dropped_required_window_byte)},
                                     {"post_last_byte_quiet_ms", std::to_string(post_last_byte_quiet_ms)},
                                 });
  return true;
}

void Proflame2TEmbedComponent::publish_decoded_rx_packet_(uint32_t export_id, const Proflame2DecodedPacket& decoded,
                                                          const uint8_t* selected_bytes, uint16_t selected_count,
                                                          uint32_t complete_ms, uint32_t post_last_byte_quiet_ms,
                                                          const char* reason) {
  std::string payload_hex;
  const uint16_t slice_start = decoded.raw_slice_start_byte;
  uint16_t slice_end = static_cast<uint16_t>(slice_start + decoded.raw_slice_length);
  if (slice_end > selected_count) {
    slice_end = selected_count;
  }
  payload_hex.reserve(static_cast<size_t>(slice_end - slice_start) * 2U);
  for (uint16_t index = slice_start; index < slice_end; index++) {
    char byte_hex[3];
    snprintf(byte_hex, sizeof(byte_hex), "%02X", selected_bytes[index]);
    payload_hex += byte_hex;
  }

  char serial_text[7];
  snprintf(serial_text, sizeof(serial_text), "%06" PRIx32, decoded.serial_id & 0xFFFFFFU);
  char cmd1_text[3];
  char cmd2_text[3];
  char err1_text[3];
  char err2_text[3];
  snprintf(cmd1_text, sizeof(cmd1_text), "%02x", decoded.cmd1);
  snprintf(cmd2_text, sizeof(cmd2_text), "%02x", decoded.cmd2);
  snprintf(err1_text, sizeof(err1_text), "%02x", decoded.err1);
  snprintf(err2_text, sizeof(err2_text), "%02x", decoded.err2);

  if (!this->display_.api_connected) {
    this->increment_rx_transport_unavailable_();
  }
  this->fire_homeassistant_event("esphome.proflame2_rx_packet",
                                 {
                                     {"schema_version", "2"},
                                     {"protocol", "proflame2"},
                                     {"event_kind", "rx_packet"},
                                     {"artifact_class", "semantic_fifo_candidate"},
                                     {"source", "lilygo_cc1101_fifo_active_listener"},
                                     {"accepted", "true"},
                                     {"qualifier", "strict"},
                                     {"payload_hex", payload_hex},
                                     {"packet_count", std::to_string(export_id)},
                                     {"freq_hz", std::to_string(this->rx_frequency_hz_)},
                                     {"device_tick_ms", std::to_string(complete_ms)},
                                     {"rssi", std::to_string(static_cast<unsigned>(this->rx_fifo_.rssi_raw_last()))},
                                     {"lqi", std::to_string(static_cast<unsigned>(this->rx_fifo_.lqi_raw_last()))},
                                     {"remote_id", serial_text},
                                     {"cmd1", cmd1_text},
                                     {"cmd2", cmd2_text},
                                     {"err1", err1_text},
                                     {"err2", err2_text},
                                     {"power", std::to_string(decoded.power)},
                                     {"flame", std::to_string(decoded.flame)},
                                     {"fan", std::to_string(decoded.fan)},
                                     {"light", std::to_string(decoded.light)},
                                     {"front", std::to_string(decoded.front)},
                                     {"aux", std::to_string(decoded.aux)},
                                     {"thermostat", std::to_string(decoded.thermostat)},
                                     {"cpi", std::to_string(decoded.cpi)},
                                     {"repeat_count", std::to_string(decoded.repeat_count)},
                                     {"confidence", std::to_string(decoded.confidence)},
                                     {"bit_offset", std::to_string(decoded.bit_offset)},
                                     {"symbol_offset", std::to_string(decoded.symbol_offset)},
                                     {"absolute_bit_offset", std::to_string(decoded.absolute_bit_offset)},
                                     {"capture_mode", "rolling_fifo_trailing_window"},
                                     {"profile", this->rx_fifo_profile_name_()},
                                     {"stop_reason", reason},
                                     {"byte_count", std::to_string(selected_count)},
                                     {"post_last_byte_quiet_ms", std::to_string(post_last_byte_quiet_ms)},
                                 });
  this->record_rx_accepted_packet_(decoded, complete_ms, selected_count);
}

void Proflame2TEmbedComponent::record_rx_dropped_packet_(const char* stage, const char* reason,
                                                         const Proflame2DecodedPacket* decoded,
                                                         const uint8_t* selected_bytes, uint16_t selected_count,
                                                         uint32_t complete_ms, uint32_t post_last_byte_quiet_ms) {
  this->rx_dropped_packet_count_++;
  const char* stage_text = stage == nullptr ? "unknown" : stage;
  const char* reason_text = reason == nullptr ? "unknown" : reason;
  if (strcmp(stage_text, "no_rf_captured") == 0) {
    this->rx_no_rf_capture_count_++;
  } else if (strcmp(stage_text, "fifo_incomplete") == 0) {
    this->rx_incomplete_fifo_count_++;
  } else if (strcmp(stage_text, "profile_mismatch") == 0) {
    this->rx_profile_mismatch_count_++;
  } else {
    this->rx_decode_failed_count_++;
  }

  std::string prefix_hex;
  prefix_hex.reserve(32U);
  const uint16_t prefix_count = std::min<uint16_t>(selected_count, 16U);
  for (uint16_t index = 0; index < prefix_count; index++) {
    append_hex_byte_(prefix_hex, selected_bytes == nullptr ? 0U : selected_bytes[index]);
  }

  const bool candidate_seen = decoded != nullptr && decoded->candidate_seen;
  const uint32_t observed_serial = candidate_seen ? decoded->serial_id : 0U;
  const uint8_t observed_cmd1 = candidate_seen ? decoded->cmd1 : 0U;
  const uint8_t observed_cmd2 = candidate_seen ? decoded->cmd2 : 0U;
  const uint8_t observed_err1 = candidate_seen ? decoded->err1 : 0U;
  const uint8_t observed_err2 = candidate_seen ? decoded->err2 : 0U;
  const uint8_t candidate_count = decoded == nullptr ? 0U : decoded->candidate_count;
  const uint16_t bit_count = static_cast<uint16_t>(selected_count * 8U);
  std::string snapshot;
  snapshot.reserve(256U);
  snapshot += "stage=";
  snapshot += stage_text;
  snapshot += " reason=";
  snapshot += reason_text;
  snapshot += " bytes=";
  snapshot += std::to_string(selected_count);
  snapshot += " bits=";
  snapshot += std::to_string(bit_count);
  snapshot += " candidates=";
  snapshot += std::to_string(candidate_count);
  snapshot += " serial=";
  append_hex24_(snapshot, observed_serial & 0xFFFFFFU);
  snapshot += " cmd=";
  append_hex_byte_(snapshot, observed_cmd1);
  append_hex_byte_(snapshot, observed_cmd2);
  snapshot += " err=";
  append_hex_byte_(snapshot, observed_err1);
  append_hex_byte_(snapshot, observed_err2);
  snapshot += " rssi=";
  append_hex_byte_(snapshot, this->rx_fifo_.rssi_raw_last());
  snapshot += " lqi=";
  append_hex_byte_(snapshot, this->rx_fifo_.lqi_raw_last());
  snapshot += " expected=";
  append_hex24_(snapshot, this->rx_active_listener_profile_.serial_id & 0xFFFFFFU);
  snapshot += " quiet_ms=";
  snapshot += std::to_string(post_last_byte_quiet_ms);
  snapshot += " tick_ms=";
  snapshot += std::to_string(complete_ms);
  snapshot += " prefix=";
  snapshot += prefix_hex;
  this->rx_last_rejection_snapshot_ = snapshot;
  ESP_LOGD(TAG, "RX active listener rejected %s", snapshot.c_str());
  this->publish_telemetry_();
}

void Proflame2TEmbedComponent::record_rx_idle_noise_(const char* reason) {
  this->rx_idle_noise_count_++;
#if PROFLAME2_TEMBED_DEBUG
  if ((this->rx_idle_noise_count_ % 100U) == 1U) {
    ESP_LOGD(TAG, "RX active listener idle/no-candidate suppressed count=%" PRIu32 " reason=%s rssi=0x%02X lqi=0x%02X",
             this->rx_idle_noise_count_, reason == nullptr ? "unknown" : reason, this->rx_fifo_.rssi_raw_last(),
             this->rx_fifo_.lqi_raw_last());
  }
#else
  (void)reason;
#endif
}

void Proflame2TEmbedComponent::record_rx_accepted_packet_(const Proflame2DecodedPacket& decoded, uint32_t complete_ms,
                                                          uint16_t selected_count) {
  this->rx_accepted_packet_count_++;
  ESP_LOGD(TAG,
           "RX active listener accepted count=%" PRIu32 " bytes=%u serial=%06" PRIx32
           " cmd1=%02x cmd2=%02x err1=%02x err2=%02x"
           " repeats=%u confidence=%u rssi=0x%02X lqi=0x%02X tick_ms=%" PRIu32,
           this->rx_accepted_packet_count_, selected_count, static_cast<uint32_t>(decoded.serial_id & 0xFFFFFFU),
           decoded.cmd1, decoded.cmd2, decoded.err1, decoded.err2, decoded.repeat_count, decoded.confidence,
           this->rx_fifo_.rssi_raw_last(), this->rx_fifo_.lqi_raw_last(), complete_ms);
  this->publish_telemetry_();
}

void Proflame2TEmbedComponent::increment_rx_suppressed_for_tx_() {
  this->rx_tx_suppressed_count_++;
  ESP_LOGD(TAG, "RX active listener suppressed for TX count=%" PRIu32, this->rx_tx_suppressed_count_);
  this->publish_telemetry_();
}

void Proflame2TEmbedComponent::increment_rx_transport_unavailable_() {
  this->rx_transport_unavailable_count_++;
  ESP_LOGD(TAG, "RX active listener accepted while API disconnected count=%" PRIu32,
           this->rx_transport_unavailable_count_);
  this->publish_telemetry_();
}

void Proflame2TEmbedComponent::restore_rx_after_tx_if_needed_() {
  if (!this->rx_fifo_paused_for_tx_) {
    return;
  }
  this->rx_fifo_paused_for_tx_ = false;
  if (!this->rx_fifo_capture_enabled_) {
    return;
  }
  this->reset_rx_fifo_rolling_capture_(millis());
  std::string error;
  this->rx_fifo_capture_configured_ = this->configure_rx_fifo_capture_mode_(error);
  if (!this->rx_fifo_capture_configured_) {
    ESP_LOGW(TAG, "RX FIFO capture restore after TX failed reason=%s",
             error.empty() ? "fifo_config_failed" : error.c_str());
  }
}

void Proflame2TEmbedComponent::maybe_log_rx_fifo_capture_status_() {
  if (!this->rx_fifo_capture_enabled_) {
    return;
  }
  const uint32_t now_ms = millis();
  if (this->rx_fifo_.status_last_log_ms() != 0U &&
      (now_ms - this->rx_fifo_.status_last_log_ms()) < RX_HEARTBEAT_LOG_INTERVAL_MS) {
    return;
  }
  this->rx_fifo_.set_status_last_log(now_ms);
  ESP_LOGD(TAG,
           "RX FIFO capture status enabled=YES mode=rolling_fifo_trailing_window"
           " strict_active_listener=%s profile=%s elapsed_ms_since_enable=%" PRIu32
           " rolling_bytes_available=%u bytes_seen_total_since_enable=%" PRIu32
           " interesting_bytes_since_export=%" PRIu32 " last_byte_age_ms=%" PRIu32
           " marcstate=0x%02X rxbytes_max=%u rxbytes_final=%u rssi=0x%02X lqi=0x%02X"
           " rolling_history_overflow=%s rx_fifo_overflow=%s export_busy=%s"
           " dropped=%" PRIu32 " accepted=%" PRIu32 " last_capture_session_index=%" PRIu32,
           YESNO(this->rx_active_listener_filter_configured_), this->rx_fifo_profile_name_(),
           this->rx_fifo_.capture_enable_tick_ms() == 0U ? 0U : now_ms - this->rx_fifo_.capture_enable_tick_ms(),
           this->rx_fifo_.rolling_count(), this->rx_fifo_.bytes_seen_since_enable(),
           this->rx_fifo_.interesting_bytes_since_export(),
           this->rx_fifo_.last_byte_tick_ms() == 0U ? 0U : now_ms - this->rx_fifo_.last_byte_tick_ms(),
           this->rx_fifo_.marcstate_last(), this->rx_fifo_.rxbytes_max(), this->rx_fifo_.rxbytes_final(),
           this->rx_fifo_.rssi_raw_last(), this->rx_fifo_.lqi_raw_last(), YESNO(this->rx_fifo_.rolling_overflow()),
           YESNO(this->rx_fifo_.hardware_overflow()), YESNO(this->rx_fifo_capture_export_busy_),
           this->rx_dropped_packet_count_, this->rx_accepted_packet_count_, this->rx_fifo_capture_session_index_);
}

void Proflame2TEmbedComponent::update_rx_runtime_display_state_() {}

void Proflame2TEmbedComponent::refresh_status_text_() {
  if (this->radio_runtime_init_state_ == RadioRuntimeInitState::FAILED ||
      this->radio_runtime_state_ == RadioRuntimeState::ERROR || this->last_tx_result_.rfind("error:", 0) == 0 ||
      this->status_text_ == "fault") {
    this->status_text_ = "fault";
  } else if (this->tx_in_progress_ || this->radio_runtime_state_ == RadioRuntimeState::TX_ACTIVE) {
    this->status_text_ = "tx";
  } else if (this->rx_fifo_capture_enabled_) {
    this->status_text_ = "ready/fifo_rx";
  } else {
    this->status_text_ = "ready";
  }
  this->update_rx_runtime_display_state_();
}

void Proflame2TEmbedComponent::publish_telemetry_() {
  this->update_display_from_telemetry_();
  TelemetryPublisher::publish_text_if_changed(this->endpoint_status_sensor_, &this->published_status_text_cache_,
                                              this->status_text_);
  TelemetryPublisher::publish_text_if_changed(this->last_error_sensor_, &this->published_last_error_cache_,
                                              this->last_error_);
  TelemetryPublisher::publish_text_if_changed(this->last_tx_result_sensor_, &this->published_last_tx_result_cache_,
                                              this->last_tx_result_);
  TelemetryPublisher::publish_text_if_changed(this->last_request_id_sensor_, &this->published_last_request_id_cache_,
                                              this->last_request_id_);
  TelemetryPublisher::publish_text_if_changed(this->last_tx_path_sensor_, &this->published_last_tx_path_cache_,
                                              this->last_tx_path_);
  TelemetryPublisher::publish_text_if_changed(this->last_payload_hex_sensor_, &this->published_last_payload_hex_cache_,
                                              this->last_payload_hex_);
  TelemetryPublisher::publish_text_if_changed(this->last_marcstate_before_tx_sensor_,
                                              &this->published_last_marcstate_before_tx_cache_,
                                              this->last_marcstate_before_tx_);
  TelemetryPublisher::publish_text_if_changed(this->last_marcstate_after_tx_sensor_,
                                              &this->published_last_marcstate_after_tx_cache_,
                                              this->last_marcstate_after_tx_);
  TelemetryPublisher::publish_text_if_changed(this->cc1101_partnum_sensor_, &this->published_cc1101_partnum_cache_,
                                              this->cc1101_partnum_);
  TelemetryPublisher::publish_text_if_changed(this->cc1101_version_sensor_, &this->published_cc1101_version_cache_,
                                              this->cc1101_version_);
  TelemetryPublisher::publish_text_if_changed(this->rx_last_rejection_snapshot_sensor_,
                                              &this->published_rx_last_rejection_snapshot_cache_,
                                              this->rx_last_rejection_snapshot_);
  TelemetryPublisher::publish_uint32_if_changed(this->tx_success_count_sensor_,
                                                &this->published_tx_success_count_cache_, this->tx_success_count_);
  TelemetryPublisher::publish_uint32_if_changed(this->tx_failure_count_sensor_,
                                                &this->published_tx_failure_count_cache_, this->tx_failure_count_);
  TelemetryPublisher::publish_uint32_if_changed(
      this->last_payload_length_sensor_, &this->published_last_payload_length_cache_, this->last_payload_length_);
  TelemetryPublisher::publish_uint8_if_changed(this->last_request_repeat_count_sensor_,
                                               &this->published_last_request_repeat_count_cache_,
                                               this->last_request_repeat_count_);
  TelemetryPublisher::publish_uint32_if_changed(this->last_tx_elapsed_ms_sensor_,
                                                &this->published_last_tx_elapsed_ms_cache_, this->last_tx_elapsed_ms_);
  TelemetryPublisher::publish_uint8_if_changed(this->tx_repeat_count_sensor_, &this->published_tx_repeat_count_cache_,
                                               this->tx_repeat_count_);
  TelemetryPublisher::publish_uint8_if_changed(this->firmware_protocol_version_sensor_,
                                               &this->published_firmware_protocol_version_cache_,
                                               this->get_firmware_protocol_version());
  TelemetryPublisher::publish_uint8_if_changed(this->config_revision_sensor_, &this->published_config_revision_cache_,
                                               this->get_config_revision());
  TelemetryPublisher::publish_uint32_if_changed(this->rx_dropped_packet_count_sensor_,
                                                &this->published_rx_dropped_packet_count_cache_,
                                                this->rx_dropped_packet_count_);
  TelemetryPublisher::publish_uint32_if_changed(this->rx_no_rf_capture_count_sensor_,
                                                &this->published_rx_no_rf_capture_count_cache_,
                                                this->rx_no_rf_capture_count_);
  TelemetryPublisher::publish_uint32_if_changed(this->rx_incomplete_fifo_count_sensor_,
                                                &this->published_rx_incomplete_fifo_count_cache_,
                                                this->rx_incomplete_fifo_count_);
  TelemetryPublisher::publish_uint32_if_changed(this->rx_decode_failed_count_sensor_,
                                                &this->published_rx_decode_failed_count_cache_,
                                                this->rx_decode_failed_count_);
  TelemetryPublisher::publish_uint32_if_changed(this->rx_profile_mismatch_count_sensor_,
                                                &this->published_rx_profile_mismatch_count_cache_,
                                                this->rx_profile_mismatch_count_);
  TelemetryPublisher::publish_uint32_if_changed(this->rx_accepted_packet_count_sensor_,
                                                &this->published_rx_accepted_packet_count_cache_,
                                                this->rx_accepted_packet_count_);
  TelemetryPublisher::publish_uint32_if_changed(this->rx_tx_suppressed_count_sensor_,
                                                &this->published_rx_tx_suppressed_count_cache_,
                                                this->rx_tx_suppressed_count_);
  TelemetryPublisher::publish_uint32_if_changed(this->rx_transport_unavailable_count_sensor_,
                                                &this->published_rx_transport_unavailable_count_cache_,
                                                this->rx_transport_unavailable_count_);
}

uint8_t Proflame2TEmbedComponent::wifi_bars_from_rssi_(float dbm) const {
  if (dbm >= -55.0f) {
    return 4;
  }
  if (dbm >= -67.0f) {
    return 3;
  }
  if (dbm >= -75.0f) {
    return 2;
  }
  return 1;
}

void Proflame2TEmbedComponent::poll_battery_status_() {
  const BatterySnapshot snapshot = this->battery_monitor_.poll(this);
  if (snapshot.gauge_valid) {
    this->set_battery_percent(snapshot.percent);
    this->set_battery_voltage(snapshot.voltage);
    if (this->battery_percent_sensor_ != nullptr &&
        (!this->battery_percent_sensor_valid_cache_ ||
         std::fabs(this->battery_percent_sensor_cache_ - snapshot.percent) > 0.1f)) {
      this->battery_percent_sensor_->publish_state(snapshot.percent);
      this->battery_percent_sensor_valid_cache_ = true;
      this->battery_percent_sensor_cache_ = snapshot.percent;
    }
    if (snapshot.charger_valid) {
      this->set_battery_usb_present(snapshot.usb_present);
      this->set_battery_charging(snapshot.charging);
    }
    return;
  }

  if (!this->display_.battery_valid ||
      this->battery_monitor_.read_failures() >= DISPLAY_BATTERY_CLEAR_FAILURE_THRESHOLD) {
    this->clear_battery();
    if (this->battery_percent_sensor_ != nullptr && this->battery_percent_sensor_valid_cache_) {
      this->battery_percent_sensor_->publish_state(NAN);
      this->battery_percent_sensor_valid_cache_ = false;
      this->battery_percent_sensor_cache_ = NAN;
    }
  }
}

void Proflame2TEmbedComponent::mark_display_dirty_() {
  if (this->display_.display_debug_mode && !this->display_.display_refresh_pending) {
    ESP_LOGD(TAG, "Display refresh pending set");
  }
  this->display_.display_refresh_pending = true;
}

void Proflame2TEmbedComponent::set_display_dimmed_(bool dimmed) {
  if (this->display_.display_dimmed == dimmed) {
    return;
  }
  this->display_.display_dimmed = dimmed;
  this->display_backlight_refresh_pending_ = true;
}

void Proflame2TEmbedComponent::set_display_dim_level(uint8_t value) {
  if (value > 10U) {
    value = 10U;
  }
  if (this->display_dim_level_ == value) {
    return;
  }
  this->display_dim_level_ = value;
  if (this->display_.display_dimmed) {
    this->display_backlight_refresh_pending_ = true;
  }
}

float Proflame2TEmbedComponent::get_display_backlight_level() const {
  if (!this->display_.display_dimmed) {
    return 1.0f;
  }
  return static_cast<float>(this->display_dim_level_) / 10.0f;
}

void Proflame2TEmbedComponent::mark_display_activity_(bool wake_display, bool center_button) {
  this->last_display_activity_ms_ = millis();
  this->display_dim_deferred_ = false;
  if ((wake_display || center_button) && this->display_.display_dimmed) {
    this->set_display_dimmed_(false);
  }
}

bool Proflame2TEmbedComponent::is_display_update_allowed() const {
  return !this->tx_in_progress_ && this->radio_runtime_state_ != RadioRuntimeState::TX_ACTIVE;
}

void Proflame2TEmbedComponent::set_wifi_connected(bool value) {
  const bool changed = this->display_.wifi_connected != value;
  this->display_.wifi_connected = value;
  if (!value) {
    this->display_.wifi_rssi_valid = false;
    this->display_.wifi_rssi_dbm = 0.0f;
    this->display_.wifi_bars = 0;
  }
  if (changed) {
    this->mark_display_dirty_();
  }
}

void Proflame2TEmbedComponent::set_api_connected(bool value) {
  if (this->display_.api_connected == value) {
    return;
  }
  this->display_.api_connected = value;
  this->mark_display_dirty_();
}

void Proflame2TEmbedComponent::handle_api_client_connected(const std::string& client_info) {
  if (!is_home_assistant_api_client_(client_info)) {
    return;
  }
  if (this->ha_api_client_count_ < UINT8_MAX) {
    this->ha_api_client_count_++;
  }
  this->set_api_connected(this->ha_api_client_count_ > 0U);
}

void Proflame2TEmbedComponent::handle_api_client_disconnected(const std::string& client_info) {
  if (!is_home_assistant_api_client_(client_info)) {
    return;
  }
  if (this->ha_api_client_count_ > 0U) {
    this->ha_api_client_count_--;
  }
  this->set_api_connected(this->ha_api_client_count_ > 0U);
}

void Proflame2TEmbedComponent::set_wifi_rssi_dbm(float value) {
  const bool changed = !this->display_.wifi_rssi_valid || std::fabs(this->display_.wifi_rssi_dbm - value) > 0.1f;
  if (!changed) {
    return;
  }
  this->display_.wifi_rssi_valid = true;
  this->display_.wifi_rssi_dbm = value;
  this->display_.wifi_bars = this->wifi_bars_from_rssi_(value);
  this->mark_display_dirty_();
}

void Proflame2TEmbedComponent::clear_wifi_rssi() {
  if (!this->display_.wifi_rssi_valid) {
    return;
  }
  this->display_.wifi_rssi_valid = false;
  this->display_.wifi_rssi_dbm = 0.0f;
  this->display_.wifi_bars = 0;
  this->mark_display_dirty_();
}

void Proflame2TEmbedComponent::set_battery_percent(float value) {
  const bool changed = !this->display_.battery_valid || std::fabs(this->display_.battery_percent - value) > 0.1f;
  if (!changed) {
    return;
  }
  this->display_.battery_valid = true;
  this->display_.battery_percent = value;
  this->mark_display_dirty_();
}

void Proflame2TEmbedComponent::set_battery_voltage(float value) {
  const bool changed = !this->display_.battery_valid || std::fabs(this->display_.battery_voltage - value) > 0.01f;
  if (!changed) {
    return;
  }
  this->display_.battery_valid = true;
  this->display_.battery_voltage = value;
  this->mark_display_dirty_();
}

void Proflame2TEmbedComponent::set_battery_charging(bool value) {
  if (this->display_.battery_charging == value) {
    return;
  }
  this->display_.battery_charging = value;
  this->mark_display_dirty_();
}

void Proflame2TEmbedComponent::set_battery_usb_present(bool value) {
  if (this->display_.battery_usb_present == value) {
    return;
  }
  this->display_.battery_usb_present = value;
  this->mark_display_dirty_();
}

void Proflame2TEmbedComponent::clear_battery() {
  if (!this->display_.battery_valid && !this->display_.battery_charging && !this->display_.battery_usb_present) {
    return;
  }
  this->display_.battery_valid = false;
  this->display_.battery_percent = 0.0f;
  this->display_.battery_voltage = 0.0f;
  this->display_.battery_charging = false;
  this->display_.battery_usb_present = false;
  this->mark_display_dirty_();
}

DisplayBodyMode Proflame2TEmbedComponent::get_display_body_mode() const {
  return DisplayController::body_mode(this->display_);
}

const char* Proflame2TEmbedComponent::get_display_body_mode_text() const {
  return DisplayController::body_mode_text(this->get_display_body_mode());
}

std::string Proflame2TEmbedComponent::get_display_header_name_text() const {
  return DisplayController::header_name_text(this->display_);
}

std::string Proflame2TEmbedComponent::get_display_battery_text() const {
  return DisplayController::battery_text(this->display_);
}

std::string Proflame2TEmbedComponent::get_display_wifi_text() const {
  return DisplayController::wifi_text(this->display_);
}

std::string Proflame2TEmbedComponent::get_display_api_text() const {
  return DisplayController::api_text(this->display_);
}

std::string Proflame2TEmbedComponent::get_display_connection_text() const {
  return DisplayController::connection_text(this->display_);
}

std::string Proflame2TEmbedComponent::get_display_mode_badge_text() const {
  if (!this->last_error_.empty() || this->status_text_.find("fault") != std::string::npos) {
    return "ERR";
  }
  if (this->display_.learn_active) {
    return "LEARN";
  }
  if (this->display_.active_operation || this->radio_runtime_state_ == RadioRuntimeState::TX_ACTIVE) {
    return "TX";
  }
  return "READY";
}

std::string Proflame2TEmbedComponent::get_display_field_text_(int value) const {
  return DisplayController::field_text(value);
}

std::string Proflame2TEmbedComponent::get_display_power_value_text() const {
  return DisplayController::power_value_text(this->display_);
}

std::string Proflame2TEmbedComponent::get_display_flame_value_text() const {
  return DisplayController::flame_value_text(this->display_);
}
std::string Proflame2TEmbedComponent::get_display_fan_value_text() const {
  return DisplayController::fan_value_text(this->display_);
}
std::string Proflame2TEmbedComponent::get_display_light_value_text() const {
  return DisplayController::light_value_text(this->display_);
}
std::string Proflame2TEmbedComponent::get_display_pilot_value_text() const {
  return DisplayController::pilot_value_text(this->display_);
}
std::string Proflame2TEmbedComponent::get_display_therm_value_text() const {
  return DisplayController::thermostat_value_text(this->display_);
}
std::string Proflame2TEmbedComponent::get_display_front_value_text() const {
  return DisplayController::front_value_text(this->display_);
}
std::string Proflame2TEmbedComponent::get_display_aux_value_text() const {
  return DisplayController::aux_value_text(this->display_);
}

std::string Proflame2TEmbedComponent::get_display_left_details_text() const {
  return DisplayController::left_details_text(this->display_);
}

std::string Proflame2TEmbedComponent::get_display_last_action_age_text() const {
  if (this->display_.last_action_millis == 0U) {
    return "never";
  }
  return DisplayController::age_text(millis() - this->display_.last_action_millis);
}

DisplayRightPanelPage Proflame2TEmbedComponent::get_effective_right_panel_page_() const {
  return DisplayController::effective_right_panel_page(this->display_);
}

const char* Proflame2TEmbedComponent::get_display_right_panel_title_text() const {
  if (this->display_.learn_active) {
    return "LEARN";
  }
  switch (this->get_effective_right_panel_page_()) {
  case DisplayRightPanelPage::LISTEN:
    return "LISTEN";
  case DisplayRightPanelPage::DEBUG:
    return "DEBUG";
  case DisplayRightPanelPage::ACTIVITY:
  default:
    return "ACTIVITY";
  }
}

std::string Proflame2TEmbedComponent::get_display_right_panel_row1_label_text() const {
  if (this->display_.learn_active) {
    return "Step";
  }
  switch (this->get_effective_right_panel_page_()) {
  case DisplayRightPanelPage::LISTEN:
    return "Status";
  case DisplayRightPanelPage::DEBUG:
    return "Request";
  case DisplayRightPanelPage::ACTIVITY:
  default:
    return "Last Action";
  }
}

std::string Proflame2TEmbedComponent::get_display_right_panel_row1_value_text() const {
  if (this->display_.learn_active) {
    return this->display_.learn_step_title;
  }
  switch (this->get_effective_right_panel_page_()) {
  case DisplayRightPanelPage::LISTEN:
    return this->rx_fifo_capture_enabled_ ? std::string("FIFO RX") : std::string("Idle");
  case DisplayRightPanelPage::DEBUG:
    return this->last_request_id_.empty() ? std::string("--") : truncate_text_(this->last_request_id_, 14U);
  case DisplayRightPanelPage::ACTIVITY:
  default:
    return this->display_.last_action_text;
  }
}

std::string Proflame2TEmbedComponent::get_display_right_panel_row2_label_text() const {
  if (this->display_.learn_active) {
    return "Do";
  }
  switch (this->get_effective_right_panel_page_()) {
  case DisplayRightPanelPage::LISTEN:
    return "Counts";
  case DisplayRightPanelPage::DEBUG:
    return "Result";
  case DisplayRightPanelPage::ACTIVITY:
  default:
    return "Result";
  }
}

std::string Proflame2TEmbedComponent::get_display_right_panel_row2_value_text() const {
  if (this->display_.learn_active) {
    return truncate_text_(this->display_.learn_instruction, 18U);
  }
  const auto result_text = [this]() -> std::string {
    const auto& error = this->last_error_;
    if (!error.empty()) {
      return "Error";
    }
    const auto& result = this->last_tx_result_;
    if (result.empty() || result == std::string("none")) {
      return "--";
    }
    if (result.find("ok") != std::string::npos || result.find("success") != std::string::npos) {
      return "OK";
    }
    return result.size() > 14 ? result.substr(0, 14) : result;
  };
  switch (this->get_effective_right_panel_page_()) {
  case DisplayRightPanelPage::LISTEN:
    return this->rx_fifo_profile_name_();
  case DisplayRightPanelPage::DEBUG:
    return result_text();
  case DisplayRightPanelPage::ACTIVITY:
  default:
    return result_text();
  }
}

std::string Proflame2TEmbedComponent::get_display_right_panel_row3_label_text() const {
  if (this->display_.learn_active) {
    return "Status";
  }
  switch (this->get_effective_right_panel_page_()) {
  case DisplayRightPanelPage::LISTEN:
    return "Why";
  case DisplayRightPanelPage::DEBUG:
    return "Elapsed";
  case DisplayRightPanelPage::ACTIVITY:
  default:
    return "Age";
  }
}

std::string Proflame2TEmbedComponent::get_display_right_panel_row3_value_text() const {
  if (this->display_.learn_active) {
    return truncate_text_(this->display_.learn_status, 18U);
  }
  switch (this->get_effective_right_panel_page_()) {
  case DisplayRightPanelPage::LISTEN:
    return str_sprintf("session %" PRIu32, this->rx_fifo_capture_session_index_);
  case DisplayRightPanelPage::DEBUG:
    return this->last_tx_elapsed_ms_ > 0U ? str_sprintf("%" PRIu32 " ms", this->last_tx_elapsed_ms_)
                                          : std::string("--");
  case DisplayRightPanelPage::ACTIVITY:
  default:
    return this->get_display_last_action_age_text();
  }
}

void Proflame2TEmbedComponent::cycle_right_panel_page_() {
  const DisplayRightPanelPage next = DisplayController::next_right_panel_page(this->display_);
  if (next == this->display_.right_panel_page) {
    return;
  }
  this->display_.right_panel_page = next;
  if (this->display_.display_debug_mode) {
    ESP_LOGD(TAG, "Right panel page changed page=%s", this->get_display_right_panel_title_text());
  }
  this->mark_display_dirty_();
}

void Proflame2TEmbedComponent::handle_center_button_press() {
  const bool was_dimmed = this->display_.display_dimmed;
  this->mark_display_activity_(true, true);
  if (was_dimmed) {
    return;
  }
  if (!this->is_display_update_allowed()) {
    return;
  }
  if (this->display_.learn_active) {
    return;
  }
  this->cycle_right_panel_page_();
}

std::string Proflame2TEmbedComponent::get_display_right_panel_text() const {
  const std::string request_id = this->last_request_id_.empty() ? "-" : truncate_text_(this->last_request_id_, 14U);
  const std::string result = truncate_text_(this->display_.last_result, 14U);
  const std::string error = this->display_.last_error.empty() ? "-" : truncate_text_(this->display_.last_error, 16U);

  if (this->display_.display_debug_mode) {
    const std::string battery_text =
        this->display_.battery_valid
            ? str_sprintf("%s %.2fV %s", get_display_battery_text().c_str(), this->display_.battery_voltage,
                          this->display_.battery_charging ? "chg"
                                                          : (this->display_.battery_usb_present ? "usb" : "bat"))
            : std::string("--%");
    const std::string rssi_text =
        this->display_.wifi_rssi_valid ? str_sprintf("%.0f dBm", this->display_.wifi_rssi_dbm) : std::string("--");
    return str_sprintf("tx:%s\nprofile:%s\nboundary:%s\nrepeat:%u bits:%" PRIu32 "\npcm:%" PRIu32
                       " row:%s\ndecode:%s\nreq:%s\nresult:%s\nerror:%s"
                       "\nelapsed:%" PRIu32 "ms ok:%" PRIu32 "/%" PRIu32 "\nmarc:%s/%s\nrssi:%s\nbat:%s",
                       this->display_.tx_mode.c_str(), this->display_.native_group_timing_profile.c_str(),
                       this->display_.native_group_repeat_boundary_mode.c_str(),
                       static_cast<unsigned>(this->display_.repeat_count), this->display_.payload_bits,
                       this->display_.pcm_row_bits, this->display_.row_prefix.c_str(),
                       this->display_.decode_result.c_str(), request_id.c_str(), result.c_str(), error.c_str(),
                       this->display_.last_tx_elapsed_ms, this->tx_success_count_, this->tx_failure_count_,
                       this->display_.marcstate_before.c_str(), this->display_.marcstate_after.c_str(),
                       rssi_text.c_str(), battery_text.c_str());
  }

  return str_sprintf("endpoint:%s\nresult:%s\nrequest:%s\nerror:%s\nradio:%s",
                     truncate_text_(this->status_text_, 16U).c_str(), result.c_str(), request_id.c_str(), error.c_str(),
                     this->display_.tx_mode.c_str());
}

void Proflame2TEmbedComponent::set_learn_mode(bool active, const std::string& step_title,
                                              const std::string& instruction, const std::string& status) {
  const bool changed = this->display_.learn_active != active ||
                       (!step_title.empty() && this->display_.learn_step_title != step_title) ||
                       (!instruction.empty() && this->display_.learn_instruction != instruction) ||
                       (!status.empty() && this->display_.learn_status != status);
  this->display_.learn_active = active;
  if (!step_title.empty()) {
    this->display_.learn_step_title = step_title;
  }
  if (!instruction.empty()) {
    this->display_.learn_instruction = instruction;
  }
  if (!status.empty()) {
    this->display_.learn_status = status;
  }
  if (changed) {
    this->mark_display_activity_(this->display_wake_on_activity_);
    this->mark_display_dirty_();
  }
}

void Proflame2TEmbedComponent::set_display_state_hint(const std::string& state_label) {
  if (this->display_.fireplace_state_label == state_label) {
    return;
  }
  this->display_.fireplace_state_label = state_label;
  this->mark_display_dirty_();
}

void Proflame2TEmbedComponent::set_display_fireplace_values(int flame, int fan, int light, int pilot, int front,
                                                            int aux, bool thermostat, bool thermostat_known) {
  const bool changed = this->display_.fireplace_flame != flame || this->display_.fireplace_fan != fan ||
                       this->display_.fireplace_light != light || this->display_.fireplace_pilot != pilot ||
                       this->display_.fireplace_front != front || this->display_.fireplace_aux != aux ||
                       this->display_.fireplace_thermostat != thermostat ||
                       this->display_.fireplace_thermostat_known != thermostat_known;
  this->display_.fireplace_flame = flame;
  this->display_.fireplace_fan = fan;
  this->display_.fireplace_light = light;
  this->display_.fireplace_pilot = pilot;
  this->display_.fireplace_front = front;
  this->display_.fireplace_aux = aux;
  this->display_.fireplace_thermostat = thermostat;
  this->display_.fireplace_thermostat_known = thermostat_known;
  if (changed) {
    this->mark_display_dirty_();
  }
}

void Proflame2TEmbedComponent::update_display_runtime_state_(const std::string& title, const std::string& detail,
                                                             uint32_t expiry_ms) {
  this->display_.active_operation = true;
  this->display_.active_operation_title = title;
  this->display_.active_operation_detail = detail;
  this->display_.active_operation_expires_millis = expiry_ms > 0 ? millis() + expiry_ms : 0U;
  this->mark_display_dirty_();
}

void Proflame2TEmbedComponent::clear_pending_display_intent_() {
  this->pending_display_intent_ = PendingDisplayIntent{};
}

void Proflame2TEmbedComponent::apply_pending_display_intent_(const std::string& request_id) {
  if (!this->pending_display_intent_.valid || this->pending_display_intent_.request_id != request_id) {
    return;
  }
  auto& intent = this->pending_display_intent_;
  if (intent.power >= 0) {
    this->display_.fireplace_power_known = true;
    this->display_.fireplace_power = intent.power != 0;
  }
  if (intent.flame >= 0)
    this->display_.fireplace_flame = intent.flame;
  if (intent.fan >= 0)
    this->display_.fireplace_fan = intent.fan;
  if (intent.light >= 0)
    this->display_.fireplace_light = intent.light;
  if (intent.pilot >= 0)
    this->display_.fireplace_pilot = intent.pilot;
  if (intent.front >= 0)
    this->display_.fireplace_front = intent.front;
  if (intent.aux >= 0)
    this->display_.fireplace_aux = intent.aux;
  if (intent.thermostat >= 0) {
    this->display_.fireplace_thermostat_known = true;
    this->display_.fireplace_thermostat = intent.thermostat != 0;
  }
  if (!intent.action_label.empty()) {
    this->display_.last_action_text = intent.action_label;
  }
  this->clear_pending_display_intent_();
}

void Proflame2TEmbedComponent::update_display_from_telemetry_() {
  this->display_.last_result = this->last_tx_result_;
  this->display_.last_error = this->last_error_;
  this->display_.tx_mode = tx_mode_to_string_(this->tx_mode_);
  this->display_.native_group_timing_profile =
      native_group_timing_profile_to_string_(this->native_group_timing_profile_);
  this->display_.native_group_repeat_boundary_mode =
      native_group_repeat_boundary_mode_to_string_(this->native_group_repeat_boundary_mode_);
  this->display_.repeat_count = this->last_request_repeat_count_;
  this->display_.payload_bits = this->last_payload_bit_length_;
  this->display_.pcm_row_bits = this->native_group_timing_profile_ == NativeGroupTimingProfile::NATIVE_REMOTE &&
                                        this->last_payload_bit_length_ > 0
                                    ? this->last_payload_bit_length_ + 1U
                                    : this->last_payload_bit_length_;
  this->display_.row_prefix = this->last_payload_hex_.size() >= 6 ? this->last_payload_hex_.substr(0, 6) : "------";
  this->display_.last_tx_elapsed_ms = this->last_tx_elapsed_ms_;
  this->display_.marcstate_before = this->last_marcstate_before_tx_;
  this->display_.marcstate_after = this->last_marcstate_after_tx_;
  if (this->display_.last_action_text.empty()) {
    this->display_.last_action_text = this->status_text_.empty() ? "Idle" : this->status_text_;
  }
  if (this->status_text_ == "ready/tx_only") {
    this->display_.fireplace_state_label = "Ready";
  } else if (this->status_text_ == "tx") {
    this->display_.fireplace_state_label = "TX";
  } else if (this->status_text_ == "fault") {
    this->display_.fireplace_state_label = "Fault";
  } else if (!this->status_text_.empty()) {
    this->display_.fireplace_state_label = this->status_text_;
  }
  this->mark_display_dirty_();
}

void Proflame2TEmbedComponent::display_state_update(int intended_power, int intended_flame, int intended_fan,
                                                    int intended_light, int intended_pilot, int intended_thermostat,
                                                    int intended_front, int intended_aux,
                                                    const std::string& intended_action_label,
                                                    const std::string& fireplace_name) {
  if (this->display_.display_debug_mode) {
    ESP_LOGD(TAG,
             "Display state update received fireplace_name=%s action_label=%s power=%d flame=%d fan=%d light=%d "
             "pilot=%d thermostat=%d front=%d aux=%d",
             fireplace_name.c_str(), intended_action_label.c_str(), intended_power, intended_flame, intended_fan,
             intended_light, intended_pilot, intended_thermostat, intended_front, intended_aux);
  }
  if (intended_power >= 0) {
    this->display_.fireplace_power_known = true;
    this->display_.fireplace_power = intended_power != 0;
  }
  if (intended_flame >= 0)
    this->display_.fireplace_flame = intended_flame;
  if (intended_fan >= 0)
    this->display_.fireplace_fan = intended_fan;
  if (intended_light >= 0)
    this->display_.fireplace_light = intended_light;
  if (intended_pilot >= 0)
    this->display_.fireplace_pilot = intended_pilot;
  if (intended_front >= 0)
    this->display_.fireplace_front = intended_front;
  if (intended_aux >= 0)
    this->display_.fireplace_aux = intended_aux;
  if (intended_thermostat >= 0) {
    this->display_.fireplace_thermostat_known = true;
    this->display_.fireplace_thermostat = intended_thermostat != 0;
  }
  if (!fireplace_name.empty()) {
    this->display_.fireplace_name = fireplace_name;
  }
  if (!intended_action_label.empty()) {
    this->display_.last_action_text = intended_action_label;
    this->display_.last_action_millis = millis();
  }
  if (this->display_.display_debug_mode) {
    ESP_LOGD(TAG, "Display state update applied fireplace_name_now=%s", this->display_.fireplace_name.c_str());
  }
  this->mark_display_dirty_();
}

bool Proflame2TEmbedComponent::tx(const std::string& request_id, const std::string& air_payload_hex,
                                  uint32_t payload_bit_length, uint8_t repeat_count, const std::string& status_text,
                                  int intended_power, int intended_flame, int intended_fan, int intended_light,
                                  int intended_pilot, int intended_thermostat, int intended_front, int intended_aux,
                                  const std::string& intended_action_label, const std::string& fireplace_name) {
  this->last_request_id_ = request_id;
  if (!fireplace_name.empty()) {
    this->display_.fireplace_name = fireplace_name;
  }
  const TxValidationConfig tx_validation_config{
      .configured_repeat_count = this->tx_repeat_count_,
      .diagnostic_repeat_count_override = this->diagnostic_repeat_count_override_,
      .payload_bit_length_override = this->payload_bit_length_override_,
  };
  TxValidationResult tx_validation =
      TxController::validate_payload_request(air_payload_hex, payload_bit_length, repeat_count, tx_validation_config);
  const uint8_t effective_repeat_count = tx_validation.prepared.effective_repeat_count;
  this->last_request_repeat_count_ = effective_repeat_count;
  this->last_tx_path_ = "cc1101_async_gdo0_msb_first";
  this->last_tx_elapsed_ms_ = 0;
  this->last_payload_hex_ = air_payload_hex;
  this->last_payload_bit_length_ = payload_bit_length;
  this->pending_display_intent_.valid = intended_power >= 0 || intended_flame >= 0 || intended_fan >= 0 ||
                                        intended_light >= 0 || intended_pilot >= 0 || intended_thermostat >= 0 ||
                                        intended_front >= 0 || intended_aux >= 0 || !intended_action_label.empty();
  this->pending_display_intent_.request_id = request_id;
  this->pending_display_intent_.power = intended_power;
  this->pending_display_intent_.flame = intended_flame;
  this->pending_display_intent_.fan = intended_fan;
  this->pending_display_intent_.light = intended_light;
  this->pending_display_intent_.pilot = intended_pilot;
  this->pending_display_intent_.thermostat = intended_thermostat;
  this->pending_display_intent_.front = intended_front;
  this->pending_display_intent_.aux = intended_aux;
  this->pending_display_intent_.action_label = intended_action_label;
  if (this->tx_debug_logging_enabled_()) {
    ESP_LOGI(TAG,
             "TX request received build_marker=%s request_id=%s air_payload_hex=%s payload_hex_length=%u "
             "payload_bits_requested=%" PRIu32
             " repeat_count=%u effective_repeat_count=%u configured_repeat_count=%u tx_path=%s tx_mode=%s "
             "native_group_timing_profile=%s native_group_repeat_boundary_mode=%s inter_frame_gap_us=%" PRIu32
             " post_frame_idle_gap_us=%" PRIu32,
             PROFLAME_BUILD_MARKER, request_id.c_str(), air_payload_hex.c_str(),
             static_cast<unsigned>(air_payload_hex.size()), payload_bit_length, repeat_count, effective_repeat_count,
             this->tx_repeat_count_, this->last_tx_path_.c_str(), tx_mode_to_string_(this->tx_mode_),
             native_group_timing_profile_to_string_(this->native_group_timing_profile_),
             native_group_repeat_boundary_mode_to_string_(this->native_group_repeat_boundary_mode_),
             this->inter_frame_gap_us_, this->post_frame_idle_gap_us_);
  }

  if (tx_validation.reject_reason == TxValidationRejectReason::INVALID_HEX_PAYLOAD) {
    this->clear_pending_display_intent_();
    this->tx_failure_count_++;
    this->last_payload_length_ = 0;
    this->last_error_ = TxController::reject_reason_to_error_code(tx_validation.reject_reason);
    this->last_tx_result_ = std::string("error:") + this->last_error_;
    ESP_LOGW(TAG, "TX rejected request_id=%s invalid hex payload length=%u", request_id.c_str(),
             static_cast<unsigned>(air_payload_hex.size()));
    this->publish_telemetry_();
    return false;
  }
  if (tx_validation.reject_reason == TxValidationRejectReason::REPEAT_COUNT_MISMATCH) {
    this->clear_pending_display_intent_();
    this->tx_failure_count_++;
    this->last_payload_length_ = 0;
    this->last_error_ = TxController::reject_reason_to_error_code(tx_validation.reject_reason);
    this->last_tx_result_ = std::string("error:") + this->last_error_;
    ESP_LOGW(TAG, "TX rejected request_id=%s repeat_count=%u expected_repeat_count=%u", request_id.c_str(),
             repeat_count, this->tx_repeat_count_);
    this->publish_telemetry_();
    return false;
  }

  this->last_payload_length_ = tx_validation.prepared.payload.size();
  if (tx_validation.reject_reason == TxValidationRejectReason::INVALID_PAYLOAD_BIT_LENGTH) {
    this->clear_pending_display_intent_();
    this->tx_failure_count_++;
    this->last_error_ = TxController::reject_reason_to_error_code(tx_validation.reject_reason);
    this->last_tx_result_ = std::string("error:") + this->last_error_;
    ESP_LOGW(TAG, "TX rejected request_id=%s invalid payload_bit_length=%" PRIu32 " payload_bytes=%u",
             request_id.c_str(), payload_bit_length, static_cast<unsigned>(tx_validation.prepared.payload.size()));
    this->publish_telemetry_();
    return false;
  }
  if (tx_validation.reject_reason == TxValidationRejectReason::INVALID_PAYLOAD_BIT_LENGTH_OVERRIDE) {
    this->clear_pending_display_intent_();
    this->tx_failure_count_++;
    this->last_error_ = TxController::reject_reason_to_error_code(tx_validation.reject_reason);
    this->last_tx_result_ = std::string("error:") + this->last_error_;
    ESP_LOGW(TAG, "TX rejected request_id=%s invalid payload_bit_length_override=%" PRIu32 " payload_bytes=%u",
             request_id.c_str(), this->payload_bit_length_override_,
             static_cast<unsigned>(tx_validation.prepared.payload.size()));
    this->publish_telemetry_();
    return false;
  }
  const uint32_t effective_payload_bit_length = tx_validation.prepared.effective_payload_bit_length;
  if (this->tx_debug_logging_enabled_()) {
    ESP_LOGI(TAG,
             "TX payload decoded request_id=%s payload_bytes=%u ha_payload_bit_length=%" PRIu32
             " payload_bit_length_override=%" PRIu32 " effective_payload_bit_length=%" PRIu32
             " diagnostic_repeat_count_override=%u",
             request_id.c_str(), static_cast<unsigned>(tx_validation.prepared.payload.size()), payload_bit_length,
             this->payload_bit_length_override_, effective_payload_bit_length, this->diagnostic_repeat_count_override_);
    log_air_payload_symbols_(request_id, tx_validation.prepared.payload, payload_bit_length,
                             effective_payload_bit_length);
    this->log_rf_path_for_tx_(tx_validation.prepared.payload.size(), effective_payload_bit_length,
                              effective_repeat_count);
  }
  const bool accepted = this->enqueue_tx_(request_id, air_payload_hex, std::move(tx_validation.prepared.payload),
                                          effective_payload_bit_length, effective_repeat_count, status_text);
  if (!accepted) {
    this->clear_pending_display_intent_();
  }
  return accepted;
}

bool Proflame2TEmbedComponent::cc1101_test_pattern(const std::string& request_id, TestPatternMode mode,
                                                   uint32_t duration_ms, uint32_t period_us,
                                                   const std::string& status_text) {
  this->last_request_id_ = request_id;
  this->last_request_repeat_count_ = 0;
  this->last_tx_path_ = "cc1101_async_test_pattern";
  this->last_tx_elapsed_ms_ = 0;
  this->last_payload_length_ = 0;
  this->last_payload_hex_ = std::string("test_pattern:") + test_pattern_mode_to_string_(mode);
  ESP_LOGW(TAG,
           "Experimental bench action request received request_id=%s mode=%s duration_ms=%" PRIu32 " period_us=%" PRIu32
           " tx_path=%s",
           request_id.c_str(), test_pattern_mode_to_string_(mode), duration_ms, period_us, this->last_tx_path_.c_str());
  this->log_rf_path_for_test_pattern_(mode, duration_ms, period_us);
  return this->enqueue_test_pattern_(request_id, duration_ms, period_us, mode, status_text);
}

bool Proflame2TEmbedComponent::is_busy_() const {
  return this->tx_in_progress_;
}

bool Proflame2TEmbedComponent::is_radio_runtime_available_() const {
  return this->radio_runtime_ != nullptr && this->radio_runtime_->task_handle != nullptr &&
         this->radio_runtime_->command_queue != nullptr && this->radio_runtime_->event_queue != nullptr;
}

bool Proflame2TEmbedComponent::ensure_radio_runtime_(RadioRuntimeStartReason reason) {
  if (this->is_radio_runtime_available_()) {
    if (this->tx_debug_logging_enabled_()) {
      ESP_LOGD(TAG, "Radio runtime ensure reused reason=%s init_state=%s state=%s",
               runtime_start_reason_to_string_(reason), runtime_init_state_to_string_(this->radio_runtime_init_state_),
               runtime_state_to_string_(this->radio_runtime_state_));
    }
    return true;
  }
  if (this->radio_runtime_init_state_ == RadioRuntimeInitState::STARTING) {
    ESP_LOGW(TAG, "Radio runtime ensure rejected while starting reason=%s", runtime_start_reason_to_string_(reason));
    return false;
  }

  if (this->tx_debug_logging_enabled_()) {
    ESP_LOGI(TAG, "Radio runtime ensure start reason=%s prior_init_state=%s failures=%" PRIu32,
             runtime_start_reason_to_string_(reason), runtime_init_state_to_string_(this->radio_runtime_init_state_),
             this->radio_runtime_create_failures_);
  }
  this->radio_runtime_init_state_ = RadioRuntimeInitState::STARTING;

  auto* runtime = new (std::nothrow) RadioRuntime();
  if (runtime == nullptr) {
    this->radio_runtime_creation_failed_ = true;
    this->radio_runtime_create_failures_++;
    this->radio_runtime_init_state_ = RadioRuntimeInitState::FAILED;
    this->radio_runtime_state_ = RadioRuntimeState::ERROR;
    ESP_LOGE(TAG, "Radio runtime allocation failed reason=%s", runtime_start_reason_to_string_(reason));
    return false;
  }
  runtime->owner = this;
  runtime->start_reason = reason;
  runtime->command_queue = xQueueCreate(1, sizeof(RadioRuntimeCommand));
  runtime->event_queue = xQueueCreate(1, sizeof(RadioRuntimeEvent));
  if (runtime->command_queue == nullptr || runtime->event_queue == nullptr) {
    if (runtime->command_queue != nullptr) {
      vQueueDelete(runtime->command_queue);
    }
    if (runtime->event_queue != nullptr) {
      vQueueDelete(runtime->event_queue);
    }
    delete runtime;
    this->radio_runtime_creation_failed_ = true;
    this->radio_runtime_create_failures_++;
    this->radio_runtime_init_state_ = RadioRuntimeInitState::FAILED;
    this->radio_runtime_state_ = RadioRuntimeState::ERROR;
    ESP_LOGE(TAG, "Radio runtime queue creation failed reason=%s", runtime_start_reason_to_string_(reason));
    return false;
  }

#if PROFLAME2_TX_CLEAN_MODE
  BaseType_t created =
      xTaskCreatePinnedToCore(radio_runtime_task_entry_, "pf2_radio", RADIO_RUNTIME_TASK_STACK_BYTES, runtime,
                              RADIO_RUNTIME_TASK_PRIORITY, &runtime->task_handle, RADIO_RUNTIME_TASK_CORE);
#else
  BaseType_t created = xTaskCreate(radio_runtime_task_entry_, "pf2_radio", RADIO_RUNTIME_TASK_STACK_BYTES, runtime,
                                   RADIO_RUNTIME_TASK_PRIORITY, &runtime->task_handle);
#endif
  if (created != pdPASS || runtime->task_handle == nullptr) {
    vQueueDelete(runtime->command_queue);
    vQueueDelete(runtime->event_queue);
    delete runtime;
    this->radio_runtime_creation_failed_ = true;
    this->radio_runtime_create_failures_++;
    this->radio_runtime_init_state_ = RadioRuntimeInitState::FAILED;
    this->radio_runtime_state_ = RadioRuntimeState::ERROR;
    ESP_LOGE(TAG, "Radio runtime task creation failed reason=%s", runtime_start_reason_to_string_(reason));
    return false;
  }
  this->radio_runtime_ = runtime;
  this->radio_runtime_creation_failed_ = false;
  this->radio_runtime_init_state_ = RadioRuntimeInitState::READY;
  this->radio_runtime_state_ = RadioRuntimeState::IDLE;
  if (this->last_error_ == "radio_runtime_create_failed") {
    this->last_error_.clear();
  }
  if (this->last_tx_result_ == "error:radio_runtime_create_failed") {
    this->last_tx_result_ = "none";
  }
  if (this->tx_debug_logging_enabled_()) {
    ESP_LOGI(TAG, "Radio runtime ready reason=%s init_state=%s state=%s worker_started=%s",
             runtime_start_reason_to_string_(reason), runtime_init_state_to_string_(this->radio_runtime_init_state_),
             runtime_state_to_string_(this->radio_runtime_state_), YESNO(runtime->worker_started));
  }
  return true;
}

bool Proflame2TEmbedComponent::enqueue_tx_(const std::string& request_id, const std::string& air_payload_hex,
                                           std::vector<uint8_t>&& payload, uint32_t payload_bit_length,
                                           uint8_t repeat_count, const std::string& status_text) {
  this->clear_deferred_debug_trace_();
  if (!this->ensure_radio_runtime_(RadioRuntimeStartReason::TX_REQUEST)) {
    this->tx_failure_count_++;
    this->last_error_ = "radio_runtime_create_failed";
    this->last_tx_result_ = "error:radio_runtime_create_failed";
    ESP_LOGW(TAG, "TX rejected request_id=%s reason=radio_runtime_create_failed", request_id.c_str());
    this->publish_telemetry_();
    return false;
  }
  if (payload.size() > RADIO_RUNTIME_MAX_PAYLOAD_BYTES) {
    this->tx_failure_count_++;
    this->last_error_ = "payload_too_large";
    this->last_tx_result_ = "error:payload_too_large";
    ESP_LOGW(TAG, "TX rejected request_id=%s payload_bytes=%u exceeds runtime capacity=%u", request_id.c_str(),
             static_cast<unsigned>(payload.size()), static_cast<unsigned>(RADIO_RUNTIME_MAX_PAYLOAD_BYTES));
    this->publish_telemetry_();
    return false;
  }
  if (this->tx_in_progress_) {
    this->tx_failure_count_++;
    this->last_error_ = "tx_busy";
    this->last_tx_result_ = "error:tx_busy";
    ESP_LOGW(TAG, "TX rejected request_id=%s reason=tx_busy state=%s", request_id.c_str(),
             runtime_state_to_string_(this->radio_runtime_state_));
    this->publish_telemetry_();
    return false;
  }
  if (this->rx_fifo_capture_enabled_) {
    this->rx_fifo_paused_for_tx_ = true;
    this->rx_fifo_capture_configured_ = false;
    this->increment_rx_suppressed_for_tx_();
    this->strobe_(CC1101_SIDLE);
    this->strobe_(CC1101_SFRX);
  }

  RadioRuntimeCommand command;
  command.kind = RadioRuntimeCommandKind::TX;
  copy_string_(command.request_id, request_id);
  copy_string_(command.air_payload_hex, air_payload_hex);
  copy_string_(command.status_text, status_text);
  command.payload_length = payload.size();
  for (size_t index = 0; index < payload.size(); index++) {
    command.payload[index] = payload[index];
  }
  command.ha_payload_bit_length = this->last_payload_bit_length_;
  command.payload_bit_length = payload_bit_length;
  command.repeat_count = repeat_count;
  command.tx_mode = this->tx_mode_;
  command.native_group_timing_profile = this->native_group_timing_profile_;
  command.native_group_repeat_boundary_mode = this->native_group_repeat_boundary_mode_;
  command.inter_frame_gap_us = this->inter_frame_gap_us_;
  command.post_frame_idle_gap_us = this->post_frame_idle_gap_us_;
  command.pre_burst_low_us = this->pre_burst_low_us_;
  command.pre_frame_low_us = this->pre_frame_low_us_;

  this->display_.last_action_text = !status_text.empty() ? status_text : "Sending";
  this->display_.last_action_millis = millis();
  this->last_error_.clear();
  this->last_tx_result_ = "queued";
  this->last_tx_elapsed_ms_ = 0;
  this->update_display_runtime_state_("Sending...", !status_text.empty() ? status_text : std::string("TX queued"), 0U);
  this->mark_display_activity_(this->display_wake_on_activity_);

  if (xQueueSend(this->radio_runtime_->command_queue, &command, 0) != pdTRUE) {
    this->display_.active_operation = false;
    this->display_.active_operation_expires_millis = 0U;
    this->display_.active_operation_title.clear();
    this->display_.active_operation_detail.clear();
    this->tx_failure_count_++;
    this->last_error_ = "tx_busy";
    this->last_tx_result_ = "error:tx_busy";
    ESP_LOGW(TAG, "TX rejected request_id=%s reason=tx_busy queue_full", request_id.c_str());
    this->publish_telemetry_();
    return false;
  }
  this->tx_in_progress_ = true;
  this->radio_runtime_state_ = RadioRuntimeState::TX_ACTIVE;
  this->refresh_status_text_();
  if (this->tx_debug_logging_enabled_()) {
    ESP_LOGI(
        TAG,
        "TX request accepted request_id=%s payload_bytes=%" PRIu32 " ha_payload_bit_length=%" PRIu32
        " payload_bit_length_override=%" PRIu32 " effective_payload_bit_length=%" PRIu32
        " repeat_count=%u tx_path=%s tx_mode=%s native_group_timing_profile=%s native_group_repeat_boundary_mode=%s",
        request_id.c_str(), this->last_payload_length_, this->last_payload_bit_length_,
        this->payload_bit_length_override_, payload_bit_length, repeat_count, this->last_tx_path_.c_str(),
        tx_mode_to_string_(this->tx_mode_), native_group_timing_profile_to_string_(this->native_group_timing_profile_),
        native_group_repeat_boundary_mode_to_string_(this->native_group_repeat_boundary_mode_));
  }
  this->publish_telemetry_();
  return true;
}

bool Proflame2TEmbedComponent::enqueue_test_pattern_(const std::string& request_id, uint32_t duration_ms,
                                                     uint32_t period_us, TestPatternMode mode,
                                                     const std::string& status_text) {
  this->refresh_status_text_();
  this->clear_deferred_debug_trace_();
  if (!this->ensure_radio_runtime_(RadioRuntimeStartReason::DIAGNOSTIC)) {
    this->tx_failure_count_++;
    this->last_error_ = "radio_runtime_create_failed";
    this->last_tx_result_ = "error:radio_runtime_create_failed";
    ESP_LOGW(TAG, "Test pattern rejected request_id=%s reason=radio_runtime_create_failed", request_id.c_str());
    this->publish_telemetry_();
    return false;
  }
  if (this->tx_in_progress_) {
    this->tx_failure_count_++;
    this->last_error_ = "tx_busy";
    this->last_tx_result_ = "error:tx_busy";
    ESP_LOGW(TAG, "Test pattern rejected request_id=%s reason=tx_busy state=%s", request_id.c_str(),
             runtime_state_to_string_(this->radio_runtime_state_));
    this->publish_telemetry_();
    return false;
  }

  RadioRuntimeCommand command;
  command.kind = RadioRuntimeCommandKind::TEST_PATTERN;
  copy_string_(command.request_id, request_id);
  copy_string_(command.air_payload_hex, std::string("test_pattern:") + test_pattern_mode_to_string_(mode));
  copy_string_(command.status_text, status_text);
  command.duration_ms = duration_ms;
  command.period_us = period_us;
  command.tx_mode = this->tx_mode_;
  command.test_pattern_mode = mode;
  this->display_.last_action_text = !status_text.empty() ? status_text : "Test pattern";
  this->display_.last_action_millis = millis();
  this->last_error_.clear();
  this->last_tx_result_ = "queued";
  this->last_tx_elapsed_ms_ = 0;
  this->update_display_runtime_state_("Sending...", !status_text.empty() ? status_text : std::string("Test pattern"),
                                      0U);
  this->mark_display_activity_(this->display_wake_on_activity_);
  if (xQueueSend(this->radio_runtime_->command_queue, &command, 0) != pdTRUE) {
    this->display_.active_operation = false;
    this->display_.active_operation_expires_millis = 0U;
    this->display_.active_operation_title.clear();
    this->display_.active_operation_detail.clear();
    this->tx_failure_count_++;
    this->last_error_ = "tx_busy";
    this->last_tx_result_ = "error:tx_busy";
    ESP_LOGW(TAG, "Test pattern rejected request_id=%s reason=tx_busy", request_id.c_str());
    this->publish_telemetry_();
    return false;
  }
  this->tx_in_progress_ = true;
  this->radio_runtime_state_ = RadioRuntimeState::TX_ACTIVE;
  this->refresh_status_text_();
  ESP_LOGW(TAG,
           "Experimental bench action accepted build_marker=%s request_id=%s mode=%s duration_ms=%" PRIu32
           " period_us=%" PRIu32,
           PROFLAME_BUILD_MARKER, request_id.c_str(), test_pattern_mode_to_string_(mode), duration_ms, period_us);
  this->publish_telemetry_();
  return true;
}

void Proflame2TEmbedComponent::log_rf_path_for_tx_(uint32_t payload_length, uint32_t effective_payload_bit_length,
                                                   uint8_t repeat_count) const {
  if (!this->tx_debug_logging_enabled_()) {
    return;
  }
  ESP_LOGI(TAG,
           "RF path tx_mode=%s payload_bytes=%" PRIu32 " effective_payload_bits=%" PRIu32
           " repeat_count=%u inter_frame_gap_us=%" PRIu32 " post_frame_idle_gap_us=%" PRIu32
           " gdo0_gpio=%d gdo2_gpio=%d data_input_assumed=%s esp32_gpio_driving=%d"
           " gdo0_output=%s gdo2_output=%s gdo0_inverted_assumed=NO gdo2_inverted_assumed=NO"
           " software_timing_trace_reflects_gpio_schedule_only=YES rf_envelope_proven=NO",
           tx_mode_to_string_(this->tx_mode_), payload_length, effective_payload_bit_length, repeat_count,
           this->inter_frame_gap_us_, this->post_frame_idle_gap_us_, gpio_pin_number_(this->cc1101_gdo0_pin_),
           gpio_pin_number_(this->cc1101_gdo2_pin_), async_tx_data_pin_to_string_(this->async_tx_data_pin_),
           this->async_tx_data_pin_ == AsyncTxDataPin::GDO2 ? gpio_pin_number_(this->cc1101_gdo2_pin_)
                                                            : gpio_pin_number_(this->cc1101_gdo0_pin_),
           YESNO(this->cc1101_gdo0_pin_ != nullptr), YESNO(this->cc1101_gdo2_pin_ != nullptr));
}

void Proflame2TEmbedComponent::log_rf_path_for_test_pattern_(TestPatternMode mode, uint32_t duration_ms,
                                                             uint32_t period_us) const {
  if (!this->tx_debug_logging_enabled_()) {
    return;
  }
  ESP_LOGI(TAG,
           "RF diagnostic mode=%s duration_ms=%" PRIu32 " period_us=%" PRIu32
           " tx_mode=%s gdo0_gpio=%d gdo2_gpio=%d data_input_assumed=%s esp32_gpio_driving=%d"
           " gdo0_output=%s gdo2_output=%s carrier_active_high_assumed=YES"
           " software_timing_trace_reflects_gpio_schedule_only=YES rf_envelope_proven=NO",
           test_pattern_mode_to_string_(mode), duration_ms, period_us, tx_mode_to_string_(this->tx_mode_),
           gpio_pin_number_(this->cc1101_gdo0_pin_), gpio_pin_number_(this->cc1101_gdo2_pin_),
           async_tx_data_pin_to_string_(this->async_tx_data_pin_),
           this->async_tx_data_pin_ == AsyncTxDataPin::GDO2 ? gpio_pin_number_(this->cc1101_gdo2_pin_)
                                                            : gpio_pin_number_(this->cc1101_gdo0_pin_),
           YESNO(this->cc1101_gdo0_pin_ != nullptr), YESNO(this->cc1101_gdo2_pin_ != nullptr));
}

void Proflame2TEmbedComponent::process_pending_operation_() {
  this->consume_radio_runtime_events_();
}

void Proflame2TEmbedComponent::clear_deferred_debug_trace_() {
  this->deferred_debug_trace_valid_ = false;
  this->deferred_debug_phase_ = 0;
  this->deferred_debug_repeat_index_ = 0;
  this->deferred_debug_bit_index_ = 0;
}

void Proflame2TEmbedComponent::store_deferred_debug_trace_(const TXTimingDiagnostics& timing, TXMode tx_mode) {
#if PROFLAME2_TEMBED_TX_DEBUG
  if (!this->tx_debug_logging_enabled_()) {
    this->clear_deferred_debug_trace_();
    return;
  }
  this->deferred_debug_trace_valid_ = true;
  this->deferred_debug_timing_ = timing;
  this->deferred_debug_tx_mode_ = tx_mode;
  this->deferred_debug_phase_ = 0;
  this->deferred_debug_repeat_index_ = 0;
  this->deferred_debug_bit_index_ = 0;
#else
  (void)timing;
  (void)tx_mode;
  this->clear_deferred_debug_trace_();
#endif
}

void Proflame2TEmbedComponent::drain_deferred_debug_trace_() {
#if PROFLAME2_TEMBED_TX_DEBUG
  if (!this->deferred_debug_trace_valid_ || !this->tx_debug_logging_enabled_()) {
    return;
  }
  const bool has_more = this->drain_debug_tx_diagnostics(
      this->deferred_debug_timing_, this->deferred_debug_tx_mode_, this->deferred_debug_phase_,
      this->deferred_debug_repeat_index_, this->deferred_debug_bit_index_);
  if (!has_more) {
    this->clear_deferred_debug_trace_();
  }
#else
  this->clear_deferred_debug_trace_();
#endif
}

void Proflame2TEmbedComponent::consume_radio_runtime_events_() {
  if (this->radio_runtime_ == nullptr || this->radio_runtime_->event_queue == nullptr) {
    return;
  }

  RadioRuntimeEvent result;
  if (xQueueReceive(this->radio_runtime_->event_queue, &result, 0) != pdTRUE) {
    return;
  }
  this->tx_in_progress_ = false;
  this->radio_runtime_state_ = result.ok ? RadioRuntimeState::IDLE : RadioRuntimeState::ERROR;
  this->last_tx_elapsed_ms_ = result.elapsed_ms;
  this->cc1101_partnum_ = format_hex_byte_(result.cc1101_partnum);
  this->cc1101_version_ = format_hex_byte_(result.cc1101_version);
  this->last_marcstate_before_tx_ = format_hex_byte_(result.marcstate_before_tx);
  this->last_marcstate_after_tx_ = format_hex_byte_(result.marcstate_after_tx);

  if (!result.ok) {
    this->tx_failure_count_++;
    this->last_error_ = string_from_buffer_(result.radio_error);
    if (this->last_error_.empty()) {
      this->last_error_ = "radio_tx_failed";
    }
    this->last_tx_result_ = "error:radio_tx_failed";
    this->status_text_ = "fault";
    const std::string attempted_action = this->pending_display_intent_.action_label;
    this->clear_pending_display_intent_();
    this->display_.last_action_text = !attempted_action.empty() ? attempted_action : "Send failed";
    this->display_.last_action_millis = millis();
    this->update_display_runtime_state_("Send failed", this->last_error_, 3000U);
    this->mark_display_activity_(this->display_wake_on_activity_);
    ESP_LOGE(TAG, "TX failed request_id=%s elapsed_ms=%" PRIu32 " error=%s",
             string_from_buffer_(result.request_id).c_str(), this->last_tx_elapsed_ms_, this->last_error_.c_str());
    this->restore_rx_after_tx_if_needed_();
    this->refresh_status_text_();
    this->publish_telemetry_();
    return;
  }

  this->tx_success_count_++;
  this->last_error_.clear();
  this->last_tx_result_ = "ok";
  this->apply_pending_display_intent_(string_from_buffer_(result.request_id));
  if (this->display_.last_action_text.empty() || this->display_.last_action_text == "Sending" ||
      this->display_.last_action_text == "Sending..." || this->display_.last_action_text == "TX queued") {
    this->display_.last_action_text = "Sent OK";
  }
  this->display_.last_action_millis = millis();
  this->update_display_runtime_state_("Sent OK", string_from_buffer_(result.request_id), 2500U);
  this->mark_display_activity_(this->display_wake_on_activity_);
  this->refresh_status_text_();

  const uint32_t repeat_count = result.kind == RadioRuntimeEventKind::TX_COMPLETE ? result.repeat_count : 1;
  const uint32_t average_repeat_duration_us =
      repeat_count > 0 ? static_cast<uint32_t>(result.timing.total_repeat_duration_us / repeat_count) : 0;
  const uint32_t average_bit_error_us =
      result.timing.bit_timing_samples > 0
          ? static_cast<uint32_t>(result.timing.bit_timing_error_total_us / result.timing.bit_timing_samples)
          : 0;
  ESP_LOGI(TAG,
           "TX result status=ok request_id=%s tx_path=%s elapsed_ms=%" PRIu32 " repeat_count=%" PRIu32
           " payload_bits=%" PRIu32 " marcstate_before=%s marcstate_after=%s",
           string_from_buffer_(result.request_id).c_str(), this->last_tx_path_.c_str(), this->last_tx_elapsed_ms_,
           repeat_count, result.payload_bit_length, this->last_marcstate_before_tx_.c_str(),
           this->last_marcstate_after_tx_.c_str());
  if (this->tx_debug_logging_enabled_()) {
    ESP_LOGI(TAG,
             "TX complete request_id=%s tx_path=%s elapsed_ms=%" PRIu32 " tx_mode=%s"
             " ha_payload_bit_length=%" PRIu32 " payload_bit_length_override=%" PRIu32
             " effective_payload_bit_length=%" PRIu32 " payload_bits_sent=%" PRIu32 " payload_bytes=%" PRIu32
             " bit_period_us=%" PRIu32 " repeat_gap_us=%" PRIu32 " measured_inter_repeat_gap_us=%" PRIu32
             " total_burst_duration_us=%" PRIu64 " repeat_duration_us[min=%" PRIu32 " max=%" PRIu32 " avg=%" PRIu32 "]"
             " bit_timing_error_us[min=%" PRIu32 " max=%" PRIu32 " avg=%" PRIu32 "]"
             " marcstate_before=%s marcstate_after=%s partnum=%s version=%s",
             string_from_buffer_(result.request_id).c_str(), this->last_tx_path_.c_str(), this->last_tx_elapsed_ms_,
             tx_mode_to_string_(result.tx_mode), result.ha_payload_bit_length, this->payload_bit_length_override_,
             result.payload_bit_length, result.timing.payload_bits, result.payload_length, result.timing.bit_period_us,
             result.timing.repeat_gap_us, result.timing.inter_repeat_gap_measured_us,
             result.timing.total_burst_duration_us,
             result.timing.min_repeat_duration_us == UINT32_MAX ? 0 : result.timing.min_repeat_duration_us,
             result.timing.max_repeat_duration_us, average_repeat_duration_us,
             result.timing.bit_timing_error_min_us == UINT32_MAX ? 0 : result.timing.bit_timing_error_min_us,
             result.timing.bit_timing_error_max_us, average_bit_error_us, this->last_marcstate_before_tx_.c_str(),
             this->last_marcstate_after_tx_.c_str(), this->cc1101_partnum_.c_str(), this->cc1101_version_.c_str());
  }
  this->store_deferred_debug_trace_(result.timing, result.tx_mode);
  this->restore_rx_after_tx_if_needed_();
  this->publish_telemetry_();
}

bool Proflame2TEmbedComponent::initialize_radio_(std::string* error) {
  std::string local_error;
  const bool ok = this->setup_async_ook_tx(this->cc1101_gdo0_pin_, this->cc1101_gdo2_pin_, this->tx_frequency_hz_,
                                           this->data_rate_bps_, local_error);
  if (!ok && error != nullptr) {
    *error = local_error.empty() ? "radio_init_failed" : local_error;
  } else if (ok) {
    this->cc1101_partnum_ = format_hex_byte_(this->read_partnum());
    this->cc1101_version_ = format_hex_byte_(this->read_version());
    this->publish_telemetry_();
  }
  return ok;
}

} // namespace proflame2_tembed
} // namespace esphome
