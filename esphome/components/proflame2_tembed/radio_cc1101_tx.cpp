#include "radio_cc1101.h"

#include <algorithm>
#include <inttypes.h>

#include "esp_cpu.h"
#include "esp_rom_sys.h"
#include "esp_timer.h"

#include "esphome/core/log.h"

namespace esphome {
namespace proflame2_tembed {

static const char* const TAG = "proflame2_cc1101";

static constexpr uint8_t CC1101_MARCSTATE_TX = 0x13;
static constexpr uint8_t CC1101_PATABLE_OOK_OFF_VALUE = 0x00;
static constexpr uint8_t CC1101_PATABLE_OOK_ON_VALUE = 0xC6;
static constexpr uint32_t CC1101_TX_STABILIZATION_TIMEOUT_US = 1500;
static constexpr uint32_t CC1101_TX_STABILIZATION_FALLBACK_US = 1000;
static constexpr uint32_t PROFLAME_NATIVE_YARDSTICK_DEFAULT_REPEAT_GAP_US = 10700;
static constexpr uint32_t PROFLAME_NATIVE_REMOTE_DEFAULT_REPEAT_GAP_US = 5240;
static constexpr uint32_t PROFLAME_NATIVE_REMOTE_SHORT_HIGH_US = 408;
static constexpr uint32_t PROFLAME_NATIVE_REMOTE_LONG_HIGH_US = 820;
static constexpr uint32_t PROFLAME_NATIVE_REMOTE_SYNC_HIGH_US = 1224;
static constexpr uint32_t PROFLAME_NATIVE_REMOTE_SHORT_LOW_US = 424;
static constexpr uint32_t PROFLAME_NATIVE_REMOTE_LONG_LOW_US = 832;
static constexpr uint32_t PROFLAME_NATIVE_REMOTE_SHORT_LOW_SCHEDULE_BIAS_US = 20;
static constexpr uint32_t PROFLAME_NATIVE_REMOTE_LONG_LOW_SCHEDULE_BIAS_US = 20;
static constexpr uint32_t PROFLAME_NATIVE_REMOTE_SHORT_HIGH_COMPENSATION_US = 12;
static constexpr uint32_t PROFLAME_NATIVE_REMOTE_LONG_HIGH_COMPENSATION_US = 12;
static constexpr uint32_t PROFLAME_NATIVE_REMOTE_SYNC_HIGH_COMPENSATION_US = 8;
static constexpr uint32_t PROFLAME_NATIVE_REMOTE_REPEAT_GAP_COMPENSATION_US = 0;
static constexpr size_t CLEAN_TIMING_MAX_BITS = 1024;
static constexpr size_t PWM_SYMBOL_MAX_SYMBOLS = 128;
static constexpr size_t PWM_SYMBOL_MAX_TRANSITIONS = 256;
static constexpr size_t PWM_SYMBOL_MAX_REPEAT_DIAGNOSTICS = 20;

#if PROFLAME2_TEMBED_TX_DEBUG
static constexpr bool TX_DEBUG_COMPILED_IN = true;
static constexpr const char* const TX_DEBUG_COMPILE_STATE = "compiled_in";
#else
static constexpr bool TX_DEBUG_COMPILED_IN = false;
static constexpr const char* const TX_DEBUG_COMPILE_STATE = "compiled_out";
#endif

static int gpio_pin_number_(GPIOPin* pin) {
  if (pin == nullptr || !pin->is_internal()) {
    return -1;
  }
  return static_cast<int>(static_cast<InternalGPIOPin*>(pin)->get_pin());
}

static bool async_tx_pin_role_conflict_(AsyncTxDataPin pin, uint8_t iocfg0, uint8_t iocfg2) {
  return pin == AsyncTxDataPin::GDO0 ? (iocfg0 != 0x2E) : (iocfg2 != 0x2E);
}

static int64_t schedule_offset_us_(uint64_t step_index, uint32_t data_rate_bps) {
  return static_cast<int64_t>((step_index * 1000000ULL + (data_rate_bps / 2U)) / data_rate_bps);
}

struct TXReadyWaitResult {
  uint32_t settle_wait_us{0};
  bool fallback_used{false};
};

static TXReadyWaitResult wait_for_tx_ready_(RadioCC1101* radio, int64_t after_enter_tx_us) {
  int64_t now_us = after_enter_tx_us;
  while ((now_us - after_enter_tx_us) < static_cast<int64_t>(CC1101_TX_STABILIZATION_TIMEOUT_US)) {
    if (radio->read_marcstate() == CC1101_MARCSTATE_TX) {
      return TXReadyWaitResult{
          .settle_wait_us = static_cast<uint32_t>(now_us - after_enter_tx_us),
          .fallback_used = false,
      };
    }
    esp_rom_delay_us(10);
    now_us = esp_timer_get_time();
  }
  const int64_t target_us = after_enter_tx_us + static_cast<int64_t>(CC1101_TX_STABILIZATION_FALLBACK_US);
  while (now_us < target_us) {
    esp_rom_delay_us(10);
    now_us = esp_timer_get_time();
  }
  return TXReadyWaitResult{
      .settle_wait_us = static_cast<uint32_t>(now_us - after_enter_tx_us),
      .fallback_used = true,
  };
}

static bool payload_bit_at_(const uint8_t* payload, uint32_t payload_bit_index) {
  const size_t byte_index = payload_bit_index / 8U;
  const int bit_index = 7 - static_cast<int>(payload_bit_index % 8U);
  return ((payload[byte_index] >> bit_index) & 0x01U) != 0;
}

struct CleanTimingScheduleEntry {
  uint32_t time_offset_cycles{0};
  bool level{false};
};

enum class PWMSymbol : uint8_t {
  SYNC = 0,
  ZERO = 1,
  ONE = 2,
  TRAILER = 3,
};

struct PWMSymbolScheduleEntry {
  uint32_t time_offset_cycles{0};
  bool level{false};
};

struct PWMSymbolTiming {
  uint32_t high_us{0};
  uint32_t low_us{0};
};

struct NativeGroupTimingProfileSpec {
  PWMSymbolTiming desired_sync{};
  PWMSymbolTiming desired_zero{};
  PWMSymbolTiming desired_one{};
  PWMSymbolTiming scheduled_sync{};
  PWMSymbolTiming scheduled_zero{};
  PWMSymbolTiming scheduled_one{};
  uint32_t desired_repeat_gap_us{0};
  uint32_t scheduled_repeat_gap_us{0};
};

static constexpr size_t PROFLAME_NATIVE_GROUP_COUNT = 7;
static constexpr size_t PROFLAME_NATIVE_SOURCE_BITS_PER_GROUP = 9;
static constexpr size_t PROFLAME_NATIVE_MAX_EMIT_BITS_PER_GROUP = 16;

struct NativePWMGroup {
  std::array<PWMSymbol, PROFLAME_NATIVE_MAX_EMIT_BITS_PER_GROUP> bits{};
  std::array<PWMSymbol, PROFLAME_NATIVE_SOURCE_BITS_PER_GROUP> source_bits{};
  PWMSymbol start_bit{PWMSymbol::ZERO};
  PWMSymbol parity_bit{PWMSymbol::ZERO};
  PWMSymbol end_bit{PWMSymbol::ZERO};
  size_t bit_count{0};
};

static uint32_t wait_until_cycle_(uint32_t target_cycle) {
  while (static_cast<int32_t>(esp_cpu_get_cycle_count() - target_cycle) < 0) {
  }
  const uint32_t actual_cycle = esp_cpu_get_cycle_count();
  return actual_cycle >= target_cycle ? (actual_cycle - target_cycle) : 0;
}

static bool decode_pwm_symbols_(const uint8_t* payload, uint32_t payload_bit_length,
                                std::array<PWMSymbol, PWM_SYMBOL_MAX_SYMBOLS>& symbols, size_t& symbol_count) {
  if (payload == nullptr || payload_bit_length == 0 || (payload_bit_length % 2U) != 0U) {
    return false;
  }
  symbol_count = payload_bit_length / 2U;
  if (symbol_count > PWM_SYMBOL_MAX_SYMBOLS) {
    return false;
  }
  for (size_t symbol_index = 0; symbol_index < symbol_count; symbol_index++) {
    const uint32_t bit_index = static_cast<uint32_t>(symbol_index * 2U);
    const bool first = payload_bit_at_(payload, bit_index);
    const bool second = payload_bit_at_(payload, bit_index + 1U);
    if (first && second) {
      symbols[symbol_index] = PWMSymbol::SYNC;
    } else if (!first && second) {
      symbols[symbol_index] = PWMSymbol::ZERO;
    } else if (first && !second) {
      symbols[symbol_index] = PWMSymbol::ONE;
    } else {
      symbols[symbol_index] = PWMSymbol::TRAILER;
    }
  }
  return true;
}

static PWMSymbolTiming pwm_symbol_timing_(PWMSymbol symbol, uint32_t bit_period_us) {
  switch (symbol) {
  case PWMSymbol::SYNC:
    return PWMSymbolTiming{bit_period_us * 3U, bit_period_us};
  case PWMSymbol::ZERO:
    return PWMSymbolTiming{bit_period_us, bit_period_us * 2U};
  case PWMSymbol::ONE:
    return PWMSymbolTiming{bit_period_us * 2U, bit_period_us};
  case PWMSymbol::TRAILER:
  default:
    return PWMSymbolTiming{0U, bit_period_us * 2U};
  }
}

static uint32_t compensated_duration_us_(uint32_t desired_us, uint32_t compensation_us, uint32_t minimum_us) {
  if (desired_us <= compensation_us) {
    return minimum_us;
  }
  return std::max<uint32_t>(minimum_us, desired_us - compensation_us);
}

static NativeGroupTimingProfileSpec native_group_timing_profile_spec_(NativeGroupTimingProfile profile,
                                                                      uint32_t bit_period_us, uint32_t repeat_gap_us) {
  NativeGroupTimingProfileSpec spec{};
  if (profile == NativeGroupTimingProfile::NATIVE_REMOTE) {
    spec.desired_sync = PWMSymbolTiming{PROFLAME_NATIVE_REMOTE_SYNC_HIGH_US, PROFLAME_NATIVE_REMOTE_LONG_LOW_US};
    spec.desired_zero = PWMSymbolTiming{PROFLAME_NATIVE_REMOTE_SHORT_HIGH_US, PROFLAME_NATIVE_REMOTE_SHORT_LOW_US};
    spec.desired_one = PWMSymbolTiming{PROFLAME_NATIVE_REMOTE_LONG_HIGH_US, PROFLAME_NATIVE_REMOTE_SHORT_LOW_US};
    spec.scheduled_sync = PWMSymbolTiming{
        compensated_duration_us_(spec.desired_sync.high_us, PROFLAME_NATIVE_REMOTE_SYNC_HIGH_COMPENSATION_US, 64U),
        spec.desired_sync.low_us + PROFLAME_NATIVE_REMOTE_LONG_LOW_SCHEDULE_BIAS_US,
    };
    spec.scheduled_zero = PWMSymbolTiming{
        compensated_duration_us_(spec.desired_zero.high_us, PROFLAME_NATIVE_REMOTE_SHORT_HIGH_COMPENSATION_US, 64U),
        spec.desired_zero.low_us + PROFLAME_NATIVE_REMOTE_SHORT_LOW_SCHEDULE_BIAS_US,
    };
    spec.scheduled_one = PWMSymbolTiming{
        compensated_duration_us_(spec.desired_one.high_us, PROFLAME_NATIVE_REMOTE_LONG_HIGH_COMPENSATION_US, 64U),
        spec.desired_one.low_us + PROFLAME_NATIVE_REMOTE_SHORT_LOW_SCHEDULE_BIAS_US,
    };
    spec.desired_repeat_gap_us = repeat_gap_us > 0 ? repeat_gap_us : PROFLAME_NATIVE_REMOTE_DEFAULT_REPEAT_GAP_US;
    spec.scheduled_repeat_gap_us =
        compensated_duration_us_(spec.desired_repeat_gap_us, PROFLAME_NATIVE_REMOTE_REPEAT_GAP_COMPENSATION_US, 1000U);
    return spec;
  }

  spec.desired_sync = PWMSymbolTiming{bit_period_us * 3U, bit_period_us};
  spec.desired_zero = PWMSymbolTiming{bit_period_us, bit_period_us};
  spec.desired_one = PWMSymbolTiming{bit_period_us * 2U, bit_period_us * 2U};
  spec.scheduled_sync = spec.desired_sync;
  spec.scheduled_zero = spec.desired_zero;
  spec.scheduled_one = spec.desired_one;
  spec.desired_repeat_gap_us = repeat_gap_us > 0 ? repeat_gap_us : PROFLAME_NATIVE_YARDSTICK_DEFAULT_REPEAT_GAP_US;
  spec.scheduled_repeat_gap_us = spec.desired_repeat_gap_us;
  return spec;
}

static PWMSymbolTiming native_group_symbol_timing_from_spec_(PWMSymbol symbol, const NativeGroupTimingProfileSpec& spec,
                                                             bool scheduled) {
  const PWMSymbolTiming sync_timing = scheduled ? spec.scheduled_sync : spec.desired_sync;
  const PWMSymbolTiming zero_timing = scheduled ? spec.scheduled_zero : spec.desired_zero;
  const PWMSymbolTiming one_timing = scheduled ? spec.scheduled_one : spec.desired_one;
  switch (symbol) {
  case PWMSymbol::SYNC:
    return sync_timing;
  case PWMSymbol::ZERO:
    return zero_timing;
  case PWMSymbol::ONE:
    return one_timing;
  case PWMSymbol::TRAILER:
  default:
    return PWMSymbolTiming{0U, one_timing.low_us};
  }
}

static const char* pwm_symbol_to_string_(PWMSymbol symbol);

static uint32_t native_remote_pcm_expected_bits_(size_t payload_length_bytes, uint32_t payload_bit_length) {
  const uint32_t available_bits = static_cast<uint32_t>(payload_length_bytes * 8U);
  if (available_bits == 0) {
    return 0;
  }
  return std::min<uint32_t>(available_bits, payload_bit_length + 1U);
}

static uint32_t native_remote_pcm_high_bits_(PWMSymbol symbol) {
  switch (symbol) {
  case PWMSymbol::SYNC:
    return 3U;
  case PWMSymbol::ZERO:
    return 1U;
  case PWMSymbol::ONE:
    return 2U;
  case PWMSymbol::TRAILER:
  default:
    return 0U;
  }
}

static bool native_remote_pcm_payload_run_matches_(const uint8_t* payload, uint32_t expected_bits,
                                                   uint32_t start_bit_index, bool bit_value, uint32_t count) {
  if (payload == nullptr || start_bit_index + count > expected_bits) {
    return false;
  }
  for (uint32_t bit_index = 0; bit_index < count; bit_index++) {
    if (payload_bit_at_(payload, start_bit_index + bit_index) != bit_value) {
      return false;
    }
  }
  return true;
}

static std::string native_remote_pcm_expected_preview_(const uint8_t* payload, uint32_t expected_bits,
                                                       uint32_t start_bit_index, uint32_t count) {
  std::string out;
  if (payload == nullptr || start_bit_index >= expected_bits) {
    return out;
  }
  const uint32_t end_bit_index = std::min<uint32_t>(expected_bits, start_bit_index + count);
  out.reserve(end_bit_index - start_bit_index);
  for (uint32_t bit_index = start_bit_index; bit_index < end_bit_index; bit_index++) {
    out.push_back(payload_bit_at_(payload, bit_index) ? '1' : '0');
  }
  return out;
}

static std::string native_remote_pcm_contribution_string_(uint32_t high_bits, uint32_t low_bits) {
  std::string out;
  out.append(high_bits, '1');
  out.append(low_bits, '0');
  return out;
}

static bool choose_native_remote_symbol_timing_from_pcm_row_(const uint8_t* payload, size_t payload_length_bytes,
                                                             uint32_t payload_bit_length,
                                                             const NativeGroupTimingProfileSpec& spec, PWMSymbol symbol,
                                                             uint32_t symbol_index, uint32_t pcm_cursor,
                                                             bool has_next_symbol, PWMSymbolTiming& timing,
                                                             uint32_t& pcm_advance_bits, std::string* diagnostic) {
  const uint32_t expected_bits = native_remote_pcm_expected_bits_(payload_length_bytes, payload_bit_length);
  const uint32_t high_bits = native_remote_pcm_high_bits_(symbol);
  if (high_bits == 0U) {
    timing = native_group_symbol_timing_from_spec_(symbol, spec, true);
    pcm_advance_bits = 0U;
    return true;
  }

  if (!native_remote_pcm_payload_run_matches_(payload, expected_bits, pcm_cursor, true, high_bits)) {
    if (diagnostic != nullptr) {
      char buffer[224];
      snprintf(buffer, sizeof(buffer),
               "symbol_index=%" PRIu32 " symbol=%s pcm_cursor=%" PRIu32
               " reason=high_run_mismatch expected=%s contribution_short=%s contribution_long=%s",
               symbol_index, pwm_symbol_to_string_(symbol), pcm_cursor,
               native_remote_pcm_expected_preview_(payload, expected_bits, pcm_cursor, 8U).c_str(),
               native_remote_pcm_contribution_string_(high_bits, 1U).c_str(),
               native_remote_pcm_contribution_string_(high_bits, 2U).c_str());
      *diagnostic = buffer;
    }
    timing = native_group_symbol_timing_from_spec_(symbol, spec, true);
    pcm_advance_bits = high_bits;
    return false;
  }

  struct Candidate {
    PWMSymbolTiming timing{};
    uint32_t low_bits{0};
  };

  std::array<Candidate, 2> candidates{};
  size_t candidate_count = 0;
  if (symbol == PWMSymbol::SYNC) {
    candidates[candidate_count++] =
        Candidate{PWMSymbolTiming{spec.scheduled_sync.high_us, spec.scheduled_sync.low_us}, 2U};
    candidates[candidate_count++] =
        Candidate{PWMSymbolTiming{spec.scheduled_sync.high_us, spec.scheduled_zero.low_us}, 1U};
  } else if (symbol == PWMSymbol::ZERO) {
    candidates[candidate_count++] =
        Candidate{PWMSymbolTiming{spec.scheduled_zero.high_us, spec.scheduled_zero.low_us}, 1U};
    candidates[candidate_count++] =
        Candidate{PWMSymbolTiming{spec.scheduled_zero.high_us, spec.scheduled_sync.low_us}, 2U};
  } else {
    candidates[candidate_count++] =
        Candidate{PWMSymbolTiming{spec.scheduled_one.high_us, spec.scheduled_zero.low_us}, 1U};
    candidates[candidate_count++] =
        Candidate{PWMSymbolTiming{spec.scheduled_one.high_us, spec.scheduled_sync.low_us}, 2U};
  }

  for (size_t candidate_index = 0; candidate_index < candidate_count; candidate_index++) {
    const Candidate& candidate = candidates[candidate_index];
    const uint32_t low_start = pcm_cursor + high_bits;
    const uint32_t next_cursor = low_start + candidate.low_bits;
    if (!native_remote_pcm_payload_run_matches_(payload, expected_bits, low_start, false, candidate.low_bits)) {
      continue;
    }
    if (has_next_symbol && next_cursor < expected_bits && !payload_bit_at_(payload, next_cursor)) {
      continue;
    }
    if (!has_next_symbol && next_cursor != expected_bits) {
      continue;
    }
    timing = candidate.timing;
    pcm_advance_bits = high_bits + candidate.low_bits;
    return true;
  }

  if (diagnostic != nullptr) {
    char buffer[256];
    snprintf(buffer, sizeof(buffer),
             "symbol_index=%" PRIu32 " symbol=%s pcm_cursor=%" PRIu32
             " reason=no_candidate_match expected=%s contribution_short=%s contribution_long=%s",
             symbol_index, pwm_symbol_to_string_(symbol), pcm_cursor,
             native_remote_pcm_expected_preview_(payload, expected_bits, pcm_cursor, 10U).c_str(),
             native_remote_pcm_contribution_string_(high_bits, 1U).c_str(),
             native_remote_pcm_contribution_string_(high_bits, 2U).c_str());
    *diagnostic = buffer;
  }
  timing = native_group_symbol_timing_from_spec_(symbol, spec, true);
  pcm_advance_bits = high_bits + (symbol == PWMSymbol::SYNC ? 2U : 1U);
  return false;
}

static const char* pwm_symbol_to_string_(PWMSymbol symbol) {
  switch (symbol) {
  case PWMSymbol::SYNC:
    return "S";
  case PWMSymbol::ZERO:
    return "0";
  case PWMSymbol::ONE:
    return "1";
  case PWMSymbol::TRAILER:
    return "Z";
  default:
    return "?";
  }
}

static char pwm_symbol_to_char_(PWMSymbol symbol) {
  switch (symbol) {
  case PWMSymbol::SYNC:
    return 'S';
  case PWMSymbol::ZERO:
    return '0';
  case PWMSymbol::ONE:
    return '1';
  case PWMSymbol::TRAILER:
    return 'Z';
  default:
    return '?';
  }
}

static std::string
native_group_repeat_symbol_list_(const std::array<NativePWMGroup, PROFLAME_NATIVE_GROUP_COUNT>& groups,
                                 size_t group_count) {
  std::string out;
  for (size_t group_index = 0; group_index < group_count; group_index++) {
    if (group_index > 0) {
      out.push_back(' ');
    }
    out.push_back('S');
    for (size_t bit_index = 0; bit_index < groups[group_index].bit_count; bit_index++) {
      out.push_back(pwm_symbol_to_char_(groups[group_index].bits[bit_index]));
    }
  }
  return out;
}

static std::string pwm_bits_to_string_(const PWMSymbol* bits, size_t bit_count) {
  std::string out;
  out.reserve(bit_count);
  for (size_t bit_index = 0; bit_index < bit_count; bit_index++) {
    out.push_back(pwm_symbol_to_char_(bits[bit_index]));
  }
  return out;
}

static std::string native_group_original_word_string_(const NativePWMGroup& group) {
  std::string out;
  out.reserve(13);
  out.push_back('S');
  out.push_back(pwm_symbol_to_char_(group.start_bit));
  for (size_t bit_index = 0; bit_index < PROFLAME_NATIVE_SOURCE_BITS_PER_GROUP; bit_index++) {
    out.push_back(pwm_symbol_to_char_(group.source_bits[bit_index]));
  }
  out.push_back(pwm_symbol_to_char_(group.parity_bit));
  out.push_back(pwm_symbol_to_char_(group.end_bit));
  return out;
}

static std::string rtl433_style_code_expectation_(const PWMSymbol* bits, size_t bit_count) {
  uint32_t value = 0;
  for (size_t bit_index = 0; bit_index < bit_count; bit_index++) {
    value <<= 1U;
    value |= (bits[bit_index] == PWMSymbol::ONE) ? 1U : 0U;
  }
  const uint32_t mask = bit_count >= 32 ? 0xFFFFFFFFU : ((1U << bit_count) - 1U);
  const uint32_t complemented = (~value) & mask;
  const uint32_t left_shift = (4U - (bit_count % 4U)) % 4U;
  const uint32_t aligned = complemented << left_shift;
  char buffer[32];
  snprintf(buffer, sizeof(buffer), "{%u}%x", static_cast<unsigned>(bit_count), aligned);
  return std::string(buffer);
}

static std::string native_group_symbol_word_(const NativePWMGroup& group) {
  std::string symbols;
  symbols.reserve(13);
  symbols.push_back('S');
  symbols.push_back(pwm_symbol_to_char_(group.start_bit));
  for (size_t bit_index = 0; bit_index < PROFLAME_NATIVE_SOURCE_BITS_PER_GROUP; bit_index++) {
    symbols.push_back(pwm_symbol_to_char_(group.source_bits[bit_index]));
  }
  symbols.push_back(pwm_symbol_to_char_(group.parity_bit));
  symbols.push_back(pwm_symbol_to_char_(group.end_bit));
  return symbols;
}

static bool native_group_air_bits_(const NativePWMGroup& group, std::string& air_bits) {
  const std::string symbols = native_group_symbol_word_(group);
  air_bits.clear();
  air_bits.reserve(symbols.size() * 2U);
  for (char ch : symbols) {
    switch (ch) {
    case 'S':
      air_bits += "11";
      break;
    case '0':
      air_bits += "01";
      break;
    case '1':
      air_bits += "10";
      break;
    case 'Z':
      air_bits += "00";
      break;
    default:
      return false;
    }
  }
  return true;
}

static std::string native_group_run_lengths_(const std::string& air_bits) {
  if (air_bits.empty()) {
    return "";
  }
  std::string out;
  char current = air_bits[0];
  size_t current_length = 1;
  for (size_t index = 1; index < air_bits.size(); index++) {
    if (air_bits[index] == current) {
      current_length++;
    } else {
      if (!out.empty()) {
        out.push_back(' ');
      }
      out.push_back(current);
      out += std::to_string(current_length);
      current = air_bits[index];
      current_length = 1;
    }
  }
  if (!out.empty()) {
    out.push_back(' ');
  }
  out.push_back(current);
  out += std::to_string(current_length);
  return out;
}

static std::string rtl433_style_code_expectation_from_string_(const std::string& bits) {
  std::array<PWMSymbol, PROFLAME_NATIVE_MAX_EMIT_BITS_PER_GROUP + 1U> symbols{};
  if (bits.size() > symbols.size()) {
    return "{invalid}";
  }
  for (size_t index = 0; index < bits.size(); index++) {
    if (bits[index] == '0') {
      symbols[index] = PWMSymbol::ZERO;
    } else if (bits[index] == '1') {
      symbols[index] = PWMSymbol::ONE;
    } else {
      return "{invalid}";
    }
  }
  return rtl433_style_code_expectation_(symbols.data(), bits.size());
}

static std::string right_shift_bits_(const std::string& bits) {
  if (bits.empty()) {
    return bits;
  }
  return std::string("0") + bits.substr(0, bits.size() - 1U);
}

static std::string left_shift_bits_(const std::string& bits) {
  if (bits.empty()) {
    return bits;
  }
  return bits.substr(1) + "0";
}

static bool derive_native_group_emit_bits_(const NativePWMGroup& group,
                                           std::array<PWMSymbol, PROFLAME_NATIVE_MAX_EMIT_BITS_PER_GROUP>& emit_bits,
                                           size_t& emit_count, std::string* failure_reason = nullptr) {
  std::string air_bits;
  if (!native_group_air_bits_(group, air_bits)) {
    if (failure_reason != nullptr) {
      *failure_reason = "air_bits_invalid";
    }
    return false;
  }

  struct Run {
    char bit;
    size_t length;
  };
  std::array<Run, 32> runs{};
  size_t run_count = 0;
  char current = air_bits[0];
  size_t current_length = 1;
  for (size_t index = 1; index < air_bits.size(); index++) {
    if (air_bits[index] == current) {
      current_length++;
    } else {
      runs[run_count++] = Run{current, current_length};
      current = air_bits[index];
      current_length = 1;
    }
  }
  runs[run_count++] = Run{current, current_length};

  if (run_count < 3 || runs[0].bit != '1' || runs[0].length != 3) {
    if (failure_reason != nullptr) {
      char buffer[48];
      snprintf(buffer, sizeof(buffer), "unexpected_first_run:%c%u", runs[0].bit, static_cast<unsigned>(runs[0].length));
      *failure_reason = buffer;
    }
    return false;
  }

  emit_count = 0;
  for (size_t run_index = 1; run_index < run_count; run_index++) {
    const Run& run = runs[run_index];
    if (run.bit != '1') {
      continue;
    }
    if (emit_count >= emit_bits.size()) {
      if (failure_reason != nullptr) {
        *failure_reason = "emit_overflow";
      }
      return false;
    }
    if (run.length == 1) {
      emit_bits[emit_count++] = PWMSymbol::ZERO;
    } else if (run.length == 2) {
      emit_bits[emit_count++] = PWMSymbol::ONE;
    } else {
      if (failure_reason != nullptr) {
        char buffer[48];
        snprintf(buffer, sizeof(buffer), "unexpected_high_run:%u", static_cast<unsigned>(run.length));
        *failure_reason = buffer;
      }
      return false;
    }
  }
  return true;
}

static bool decode_native_pwm_groups_(const uint8_t* payload, uint32_t payload_bit_length,
                                      std::array<NativePWMGroup, PROFLAME_NATIVE_GROUP_COUNT>& groups,
                                      size_t& group_count, size_t& trailing_symbol_count,
                                      std::string* failure_reason = nullptr) {
  constexpr size_t SYMBOLS_PER_WORD = 13;
  constexpr size_t WORD_SYMBOL_COUNT = PROFLAME_NATIVE_GROUP_COUNT * SYMBOLS_PER_WORD;

  std::array<PWMSymbol, PWM_SYMBOL_MAX_SYMBOLS> symbols{};
  size_t symbol_count = 0;
  if (!decode_pwm_symbols_(payload, payload_bit_length, symbols, symbol_count)) {
    if (failure_reason != nullptr) {
      *failure_reason = "decode_pwm_symbols_failed";
    }
    return false;
  }
  if (symbol_count < WORD_SYMBOL_COUNT) {
    if (failure_reason != nullptr) {
      char buffer[64];
      snprintf(buffer, sizeof(buffer), "symbol_count_too_small:%u<%u", static_cast<unsigned>(symbol_count),
               static_cast<unsigned>(WORD_SYMBOL_COUNT));
      *failure_reason = buffer;
    }
    return false;
  }

  group_count = PROFLAME_NATIVE_GROUP_COUNT;
  trailing_symbol_count = symbol_count - WORD_SYMBOL_COUNT;
  for (size_t group_index = 0; group_index < group_count; group_index++) {
    const size_t base = group_index * SYMBOLS_PER_WORD;
    if (symbols[base] != PWMSymbol::SYNC) {
      if (failure_reason != nullptr) {
        char buffer[64];
        snprintf(buffer, sizeof(buffer), "group%u_missing_sync:%c", static_cast<unsigned>(group_index),
                 pwm_symbol_to_char_(symbols[base]));
        *failure_reason = buffer;
      }
      return false;
    }
    auto& group = groups[group_index];
    group.start_bit = symbols[base + 1U];
    group.parity_bit = symbols[base + 11U];
    group.end_bit = symbols[base + 12U];
    if ((group.start_bit != PWMSymbol::ZERO && group.start_bit != PWMSymbol::ONE) ||
        (group.parity_bit != PWMSymbol::ZERO && group.parity_bit != PWMSymbol::ONE) ||
        (group.end_bit != PWMSymbol::ZERO && group.end_bit != PWMSymbol::ONE)) {
      if (failure_reason != nullptr) {
        char buffer[80];
        snprintf(buffer, sizeof(buffer), "group%u_guard_invalid:start=%c parity=%c end=%c",
                 static_cast<unsigned>(group_index), pwm_symbol_to_char_(group.start_bit),
                 pwm_symbol_to_char_(group.parity_bit), pwm_symbol_to_char_(group.end_bit));
        *failure_reason = buffer;
      }
      return false;
    }
    for (size_t bit_index = 0; bit_index < PROFLAME_NATIVE_SOURCE_BITS_PER_GROUP; bit_index++) {
      const PWMSymbol bit_symbol = symbols[base + 2U + bit_index];
      if (bit_symbol != PWMSymbol::ZERO && bit_symbol != PWMSymbol::ONE) {
        if (failure_reason != nullptr) {
          char buffer[64];
          snprintf(buffer, sizeof(buffer), "group%u_data_invalid[%u]=%c", static_cast<unsigned>(group_index),
                   static_cast<unsigned>(bit_index), pwm_symbol_to_char_(bit_symbol));
          *failure_reason = buffer;
        }
        return false;
      }
      group.source_bits[bit_index] = bit_symbol;
    }
    std::string derive_failure;
    if (!derive_native_group_emit_bits_(group, group.bits, group.bit_count, &derive_failure)) {
      if (failure_reason != nullptr) {
        char buffer[128];
        snprintf(buffer, sizeof(buffer), "group%u_derive_failed:%s", static_cast<unsigned>(group_index),
                 derive_failure.c_str());
        *failure_reason = buffer;
      }
      return false;
    }
  }

  for (size_t symbol_index = WORD_SYMBOL_COUNT; symbol_index < symbol_count; symbol_index++) {
    if (symbols[symbol_index] != PWMSymbol::TRAILER) {
      if (failure_reason != nullptr) {
        char buffer[64];
        snprintf(buffer, sizeof(buffer), "trailer_invalid[%u]=%c",
                 static_cast<unsigned>(symbol_index - WORD_SYMBOL_COUNT), pwm_symbol_to_char_(symbols[symbol_index]));
        *failure_reason = buffer;
      }
      return false;
    }
  }
  return true;
}

#if PROFLAME2_TEMBED_TX_DEBUG
static constexpr uint8_t MARCSTATE_UNAVAILABLE = 0xFF;

static void capture_bit_timing_sample_(TXTimingDiagnostics& timing, uint32_t payload_bit_index, bool high,
                                       int64_t target_offset_us, int64_t actual_offset_us, uint32_t timing_error_us) {
  if (timing.bit_timing_trace_count >= TXTimingDiagnostics::BIT_TIMING_SAMPLE_CAPACITY) {
    return;
  }
  auto& sample = timing.bit_timing_trace[timing.bit_timing_trace_count++];
  sample.bit_index = payload_bit_index;
  sample.bit_value = high ? 1U : 0U;
  sample.target_offset_us = target_offset_us;
  sample.actual_offset_us = actual_offset_us;
  sample.timing_error_us = timing_error_us;
}

static void capture_repeat_timing_sample_(TXTimingDiagnostics& timing, uint8_t repeat_index, uint8_t repeat_count,
                                          int64_t repeat_start_us, int64_t previous_repeat_end_us, int64_t first_bit_us,
                                          int64_t repeat_end_us, uint32_t actual_gap_from_previous_end_to_first_bit_us,
                                          uint32_t setup_duration_before_first_bit_us, uint32_t frame_duration_us,
                                          uint64_t total_burst_duration_us, uint8_t strobe_sidle_status,
                                          uint8_t strobe_sftx_status, uint8_t strobe_stx_status,
                                          uint8_t marcstate_after_enter_tx, uint8_t marcstate_after_repeat) {
  if (timing.repeat_timing_trace_count >= TXTimingDiagnostics::REPEAT_TRACE_CAPACITY) {
    return;
  }
  auto& sample = timing.repeat_timing_trace[timing.repeat_timing_trace_count++];
  sample.repeat_index = repeat_index;
  sample.repeat_count = repeat_count;
  sample.repeat_start_us = repeat_start_us;
  sample.previous_repeat_end_us = previous_repeat_end_us;
  sample.first_bit_us = first_bit_us;
  sample.repeat_end_us = repeat_end_us;
  sample.actual_gap_from_previous_end_to_first_bit_us = actual_gap_from_previous_end_to_first_bit_us;
  sample.setup_duration_before_first_bit_us = setup_duration_before_first_bit_us;
  sample.frame_duration_us = frame_duration_us;
  sample.total_burst_duration_us = total_burst_duration_us;
  sample.strobe_sidle_status = strobe_sidle_status;
  sample.strobe_sftx_status = strobe_sftx_status;
  sample.strobe_stx_status = strobe_stx_status;
  sample.marcstate_after_enter_tx = marcstate_after_enter_tx;
  sample.marcstate_after_repeat = marcstate_after_repeat;
}
#endif

bool RadioCC1101::setup_async_ook_tx(GPIOPin* gdo0_pin, GPIOPin* gdo2_pin, uint32_t frequency_hz,
                                     uint32_t data_rate_bps, std::string& error) {
  this->gdo0_pin_ = gdo0_pin;
  this->gdo2_pin_ = gdo2_pin;
  this->frequency_hz_ = frequency_hz;
  this->data_rate_bps_ = data_rate_bps;

  if (this->async_tx_pin_() == nullptr) {
    error = this->async_tx_data_pin_ == AsyncTxDataPin::GDO0 ? "gdo0_pin_missing" : "gdo2_pin_missing";
    return false;
  }

  if (this->gdo0_pin_ != nullptr) {
    this->gdo0_pin_->setup();
    this->gdo0_pin_->digital_write(false);
  }
  if (this->gdo2_pin_ != nullptr) {
    this->gdo2_pin_->setup();
    this->gdo2_pin_->digital_write(false);
  }

  ESP_LOGCONFIG(TAG, "Async TX data path: firmware drives %s (esp32_gpio=%d)",
                async_tx_data_pin_to_string_(this->async_tx_data_pin_), gpio_pin_number_(this->async_tx_pin_()));
  if (this->async_tx_data_pin_ == AsyncTxDataPin::GDO2) {
    ESP_LOGW(TAG, "Async TX data path override active: using GDO2 test path although CC1101 documentation names GDO0 "
                  "for async TX data");
  }
  ESP_LOGCONFIG(TAG, "TX detailed diagnostics %s (PROFLAME2_TEMBED_TX_DEBUG=%u)", TX_DEBUG_COMPILE_STATE,
                static_cast<unsigned>(TX_DEBUG_COMPILED_IN ? 1U : 0U));

  this->strobe_(CC1101_SRES);
  esp_rom_delay_us(1000);
  if (!this->apply_async_ook_registers_(error)) {
    this->initialized_ = false;
    return false;
  }
  ESP_LOGCONFIG(TAG, "CC1101 identity partnum=0x%02X version=0x%02X", this->read_partnum(), this->read_version());
  this->set_idle();
  this->initialized_ = true;
  return true;
}

void RadioCC1101::log_rf_output_path(GPIOPin* gdo0_pin, GPIOPin* gdo2_pin, TXMode tx_mode, uint32_t payload_length,
                                     uint32_t payload_bit_length, uint8_t repeat_count, uint32_t repeat_gap_us) {
  const uint8_t iocfg2 = this->read_register_(CC1101_IOCFG2);
  const uint8_t iocfg0 = this->read_register_(CC1101_IOCFG0);
  const uint8_t pktctrl1 = this->read_register_(CC1101_PKTCTRL1);
  const uint8_t pktctrl0 = this->read_register_(CC1101_PKTCTRL0);
  const bool async_serial_tx_enabled = (pktctrl0 & 0x30U) == 0x30U;
  const bool pin_role_conflict = async_tx_pin_role_conflict_(this->async_tx_data_pin_, iocfg0, iocfg2);
  ESP_LOGI(TAG,
           "RF output path tx_mode=%s payload_bytes=%" PRIu32 " payload_bits=%" PRIu32
           " repeat_count=%u repeat_gap_us=%" PRIu32 " gdo0_gpio=%d gdo2_gpio=%d async_serial_mode=%s"
           " IOCFG0=0x%02X IOCFG2=0x%02X PKTCTRL1=0x%02X PKTCTRL0=0x%02X"
           " data_input_assumed=%s esp32_gpio_driving=%d"
           " gdo0_active_high_assumed=YES gdo0_inverted=NO "
           "software_timing_trace_reflects_intended_gpio_edges_only=YES",
           tx_mode_to_string_(tx_mode), payload_length, payload_bit_length, repeat_count, repeat_gap_us,
           gpio_pin_number_(gdo0_pin), gpio_pin_number_(gdo2_pin), YESNO(async_serial_tx_enabled), iocfg0, iocfg2,
           pktctrl1, pktctrl0, async_tx_data_pin_to_string_(this->async_tx_data_pin_),
           gpio_pin_number_(this->async_tx_pin_()));
  if (pin_role_conflict) {
    ESP_LOGE(TAG, "ERROR: CC1101 async TX pin role conflict detected data_input_assumed=%s IOCFG0=0x%02X IOCFG2=0x%02X",
             async_tx_data_pin_to_string_(this->async_tx_data_pin_), iocfg0, iocfg2);
  }
}

bool RadioCC1101::apply_async_ook_registers_(std::string& error, bool log_config) {
  const uint32_t frequency_word = compute_frequency_word_(this->frequency_hz_);
  uint8_t mdmcfg4 = 0;
  uint8_t mdmcfg3 = 0;
  compute_drate_registers_(this->data_rate_bps_, mdmcfg4, mdmcfg3);

  const uint8_t pa_table[8] = {
      CC1101_PATABLE_OOK_OFF_VALUE, CC1101_PATABLE_OOK_ON_VALUE, CC1101_PATABLE_OOK_ON_VALUE,
      CC1101_PATABLE_OOK_ON_VALUE,  CC1101_PATABLE_OOK_ON_VALUE, CC1101_PATABLE_OOK_ON_VALUE,
      CC1101_PATABLE_OOK_ON_VALUE,  CC1101_PATABLE_OOK_ON_VALUE,
  };

  this->write_register_(CC1101_IOCFG2, 0x2E);
  this->write_register_(CC1101_IOCFG1, 0x2E);
  this->write_register_(CC1101_IOCFG0, 0x2E);
  this->write_register_(CC1101_FIFOTHR, 0x47);
  this->write_register_(CC1101_PKTLEN, 0xFF);
  this->write_register_(CC1101_PKTCTRL1, 0x04);
  this->write_register_(CC1101_PKTCTRL0, 0x32);
  this->write_register_(CC1101_FSCTRL1, 0x06);
  this->write_register_(CC1101_FSCTRL0, 0x00);
  this->write_register_(CC1101_FREQ2, static_cast<uint8_t>((frequency_word >> 16) & 0xFF));
  this->write_register_(CC1101_FREQ1, static_cast<uint8_t>((frequency_word >> 8) & 0xFF));
  this->write_register_(CC1101_FREQ0, static_cast<uint8_t>(frequency_word & 0xFF));
  this->write_register_(CC1101_MDMCFG4, mdmcfg4);
  this->write_register_(CC1101_MDMCFG3, mdmcfg3);
  this->write_register_(CC1101_MDMCFG2, 0x30);
  this->write_register_(CC1101_MDMCFG1, 0x22);
  this->write_register_(CC1101_MDMCFG0, 0xF8);
  this->write_register_(CC1101_DEVIATN, 0x00);
  this->write_register_(CC1101_MCSM1, 0x30);
  this->write_register_(CC1101_MCSM0, 0x18);
  this->write_register_(CC1101_FOCCFG, 0x16);
  this->write_register_(CC1101_BSCFG, 0x6C);
  this->write_register_(CC1101_AGCCTRL2, 0x43);
  this->write_register_(CC1101_AGCCTRL1, 0x40);
  this->write_register_(CC1101_AGCCTRL0, 0x91);
  this->write_register_(CC1101_FREND1, 0x56);
  this->write_register_(CC1101_FREND0, 0x11);
  this->write_register_(CC1101_FSCAL3, 0xE9);
  this->write_register_(CC1101_FSCAL2, 0x2A);
  this->write_register_(CC1101_FSCAL1, 0x00);
  this->write_register_(CC1101_FSCAL0, 0x1F);
  this->write_register_(CC1101_TEST2, 0x81);
  this->write_register_(CC1101_TEST1, 0x35);
  this->write_register_(CC1101_TEST0, 0x09);
  this->write_burst_register_(CC1101_PATABLE, pa_table, sizeof(pa_table));

  this->strobe_(CC1101_SCAL);
  esp_rom_delay_us(1000);

  if (log_config) {
    ESP_LOGCONFIG(TAG,
                  "CC1101 async OOK TX configured frequency_hz=%" PRIu32 " data_rate_bps=%" PRIu32
                  " mdmcfg4=0x%02X mdmcfg3=0x%02X",
                  this->frequency_hz_, this->data_rate_bps_, mdmcfg4, mdmcfg3);
    ESP_LOGCONFIG(TAG, "CC1101 async OOK PA levels logic0=0x%02X logic1=0x%02X FREND0.PA_POWER=%u",
                  CC1101_PATABLE_OOK_OFF_VALUE, CC1101_PATABLE_OOK_ON_VALUE,
                  static_cast<unsigned>(this->read_register_(CC1101_FREND0) & 0x07U));
    ESP_LOGCONFIG(TAG,
                  "CC1101 async serial TX path data_input_assumed=%s expected_pin_role=high_z_no_cc1101_output_function"
                  " IOCFG0=0x%02X IOCFG2=0x%02X PKTCTRL0=0x%02X PKTCTRL1=0x%02X",
                  async_tx_data_pin_to_string_(this->async_tx_data_pin_), this->read_register_(CC1101_IOCFG0),
                  this->read_register_(CC1101_IOCFG2), this->read_register_(CC1101_PKTCTRL0),
                  this->read_register_(CC1101_PKTCTRL1));
    this->log_register_snapshot_();
  }
  error.clear();
  return true;
}

bool RadioCC1101::enter_tx_mode_(std::string& error) {
  this->last_sidled_status_ = this->strobe_(CC1101_SIDLE);
  this->last_sftx_status_ = this->strobe_(CC1101_SFTX);
  this->last_stx_status_ = this->strobe_(CC1101_STX);
  error.clear();
  return true;
}

void RadioCC1101::log_register_snapshot_() {
  const uint8_t pa_table0 = this->read_register_(CC1101_PATABLE);
  std::array<uint8_t, 8> pa_table{};
  this->read_burst_register_(CC1101_PATABLE, pa_table.data(), pa_table.size());
  ESP_LOGCONFIG(TAG, "CC1101 partnum=0x%02X version=0x%02X", this->read_partnum(), this->read_version());
  ESP_LOGCONFIG(TAG, "CC1101 IOCFG2=0x%02X IOCFG0=0x%02X", this->read_register_(CC1101_IOCFG2),
                this->read_register_(CC1101_IOCFG0));
  ESP_LOGCONFIG(TAG, "CC1101 PKTCTRL1=0x%02X PKTCTRL0=0x%02X", this->read_register_(CC1101_PKTCTRL1),
                this->read_register_(CC1101_PKTCTRL0));
  ESP_LOGCONFIG(TAG, "CC1101 async serial TX configured=%s data_input_assumed=%s",
                YESNO((this->read_register_(CC1101_PKTCTRL0) & 0x30U) == 0x30U),
                async_tx_data_pin_to_string_(this->async_tx_data_pin_));
  ESP_LOGCONFIG(TAG, "CC1101 FSCTRL1=0x%02X FSCTRL0=0x%02X", this->read_register_(CC1101_FSCTRL1),
                this->read_register_(CC1101_FSCTRL0));
  ESP_LOGCONFIG(TAG, "CC1101 FREQ2=0x%02X FREQ1=0x%02X FREQ0=0x%02X", this->read_register_(CC1101_FREQ2),
                this->read_register_(CC1101_FREQ1), this->read_register_(CC1101_FREQ0));
  ESP_LOGCONFIG(TAG, "CC1101 MDMCFG4=0x%02X MDMCFG3=0x%02X MDMCFG2=0x%02X MDMCFG1=0x%02X MDMCFG0=0x%02X",
                this->read_register_(CC1101_MDMCFG4), this->read_register_(CC1101_MDMCFG3),
                this->read_register_(CC1101_MDMCFG2), this->read_register_(CC1101_MDMCFG1),
                this->read_register_(CC1101_MDMCFG0));
  ESP_LOGCONFIG(TAG, "CC1101 DEVIATN=0x%02X", this->read_register_(CC1101_DEVIATN));
  ESP_LOGCONFIG(TAG, "CC1101 FREND1=0x%02X FREND0=0x%02X", this->read_register_(CC1101_FREND1),
                this->read_register_(CC1101_FREND0));
  ESP_LOGCONFIG(TAG, "CC1101 MCSM1=0x%02X MCSM0=0x%02X", this->read_register_(CC1101_MCSM1),
                this->read_register_(CC1101_MCSM0));
  ESP_LOGCONFIG(TAG, "CC1101 PATABLE0=0x%02X", pa_table0);
  ESP_LOGCONFIG(TAG,
                "CC1101 PATABLE effective_pa_entry0=0x%02X [0]=0x%02X [1]=0x%02X [2]=0x%02X [3]=0x%02X [4]=0x%02X "
                "[5]=0x%02X [6]=0x%02X [7]=0x%02X",
                pa_table[0], pa_table[0], pa_table[1], pa_table[2], pa_table[3], pa_table[4], pa_table[5], pa_table[6],
                pa_table[7]);
}

const char* RadioCC1101::tx_mode_to_string_(TXMode tx_mode) {
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

const char* RadioCC1101::test_pattern_mode_to_string_(TestPatternMode mode) {
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

const char* RadioCC1101::async_tx_data_pin_to_string_(AsyncTxDataPin pin) {
  switch (pin) {
  case AsyncTxDataPin::GDO0:
    return "GDO0";
  case AsyncTxDataPin::GDO2:
    return "GDO2";
  default:
    return "unknown";
  }
}

const char* RadioCC1101::native_group_timing_profile_to_string_(NativeGroupTimingProfile profile) {
  switch (profile) {
  case NativeGroupTimingProfile::YARDSTICK_COMPAT:
    return "yardstick_compat";
  case NativeGroupTimingProfile::NATIVE_REMOTE:
    return "native_remote";
  default:
    return "unknown";
  }
}

const char* RadioCC1101::native_group_repeat_boundary_mode_to_string_(NativeGroupRepeatBoundaryMode mode) {
  switch (mode) {
  case NativeGroupRepeatBoundaryMode::CONTINUOUS_TX:
    return "continuous_tx";
  case NativeGroupRepeatBoundaryMode::REENTER_TX:
    return "reenter_tx";
  default:
    return "unknown";
  }
}

GPIOPin* RadioCC1101::async_tx_pin_() const {
  return this->async_tx_data_pin_ == AsyncTxDataPin::GDO2 ? this->gdo2_pin_ : this->gdo0_pin_;
}

bool RadioCC1101::transmit_async_ook(const uint8_t* payload, size_t length, uint32_t payload_bit_length,
                                     uint8_t repeat_count, uint32_t repeat_gap_us, TXMode tx_mode,
                                     NativeGroupTimingProfile native_group_timing_profile,
                                     NativeGroupRepeatBoundaryMode native_group_repeat_boundary_mode,
                                     uint32_t pre_burst_low_us, uint32_t pre_frame_low_us,
                                     uint32_t post_frame_idle_gap_us, uint32_t& elapsed_ms, TXTimingDiagnostics& timing,
                                     std::string& error) {
  elapsed_ms = 0;
  timing = TXTimingDiagnostics{};
  if (!this->initialized_) {
    error = "radio_not_initialized";
    return false;
  }
  if (payload == nullptr || length == 0) {
    error = "empty_payload";
    return false;
  }
  if (payload_bit_length == 0 || payload_bit_length > (length * 8U)) {
    error = "invalid_payload_bit_length";
    return false;
  }
  if (this->data_rate_bps_ == 0) {
    error = "invalid_data_rate";
    return false;
  }

  const uint32_t bit_period_us =
      std::max<uint32_t>(1, static_cast<uint32_t>((1000000ULL + (this->data_rate_bps_ / 2U)) / this->data_rate_bps_));
  timing.payload_bits = payload_bit_length;
  timing.bit_period_us = bit_period_us;
  timing.repeat_gap_us = repeat_gap_us;
  const int64_t started_us = esp_timer_get_time();
  int64_t burst_first_bit_us = 0;
  int64_t previous_repeat_end_us = 0;
#if PROFLAME2_TEMBED_TX_DEBUG
  capture_first_bits_(timing, payload, payload_bit_length);
#endif

  // Timing-sensitive TX implementation boundary.
  //
  // The code below intentionally keeps each RF emission mode monolithic. The
  // order of preload writes, enter-TX, TX-ready waits, scheduled waits,
  // GDO pin writes, repeat boundaries, and set-idle calls is RF-visible.
  // Splitting the timing paths into ordinary helper functions risks changing
  // generated code, call timing, stack use, or instruction ordering enough to
  // break rtl_433/fireplace acceptance. Prefer comments and non-timing cleanup
  // around this block; do not decompose the emission paths without a deliberate
  // timing-validation effort.
#if !PROFLAME2_TX_CLEAN_MODE
  if (tx_mode == TXMode::CLEAN_TIMING_TEST) {
    error = "clean_timing_mode_disabled";
    return false;
  }
#endif

#if PROFLAME2_TX_CLEAN_MODE
  if (tx_mode == TXMode::CLEAN_TIMING_TEST) {
    if (payload_bit_length > CLEAN_TIMING_MAX_BITS) {
      error = "clean_timing_payload_too_large";
      return false;
    }

    const uint32_t cycles_per_us = std::max<uint32_t>(1, esp_rom_get_cpu_ticks_per_us());
    const uint32_t cycles_per_bit = std::max<uint32_t>(1, bit_period_us * cycles_per_us);
    std::array<CleanTimingScheduleEntry, CLEAN_TIMING_MAX_BITS> schedule{};
    for (uint32_t payload_bit_index = 1; payload_bit_index < payload_bit_length; payload_bit_index++) {
      schedule[payload_bit_index - 1].time_offset_cycles = cycles_per_bit * payload_bit_index;
      schedule[payload_bit_index - 1].level = payload_bit_at_(payload, payload_bit_index);
    }

    // TX timing-critical region begins here. The clean timing test path is
    // intentionally shaped around preload, enter-TX, wait, pin-write, and
    // set-idle ordering; do not restructure this loop without timing evidence.
    for (uint8_t transmission_index = 0; transmission_index < repeat_count; transmission_index++) {
      const int64_t repeat_start_us = esp_timer_get_time();
      int64_t desired_first_bit_us = repeat_start_us;
      if (transmission_index > 0 && previous_repeat_end_us > 0) {
        desired_first_bit_us = previous_repeat_end_us + static_cast<int64_t>(repeat_gap_us);
      }
      const bool preload_first_bit = (pre_frame_low_us == 0 && repeat_gap_us == 0);
      const bool first_bit_level = payload_bit_at_(payload, 0);
      if (preload_first_bit) {
        this->async_tx_pin_()->digital_write(first_bit_level);
      } else {
        this->async_tx_pin_()->digital_write(false);
      }
      if (!this->enter_tx_mode_(error)) {
        this->set_idle();
        return false;
      }
      const int64_t after_enter_tx_us = esp_timer_get_time();
      const TXReadyWaitResult tx_ready_wait = wait_for_tx_ready_(this, after_enter_tx_us);
      const int64_t tx_ready_us = after_enter_tx_us + static_cast<int64_t>(tx_ready_wait.settle_wait_us);
      if (desired_first_bit_us < tx_ready_us) {
        desired_first_bit_us = tx_ready_us;
      }
      if (pre_frame_low_us > 0) {
        desired_first_bit_us =
            std::max(desired_first_bit_us, after_enter_tx_us + static_cast<int64_t>(pre_frame_low_us));
      }
      if (!preload_first_bit) {
        this->async_tx_pin_()->digital_write(false);
      }
      wait_until_(desired_first_bit_us);
      const int64_t first_bit_us = esp_timer_get_time();
      const uint32_t setup_duration_us =
          after_enter_tx_us >= repeat_start_us ? static_cast<uint32_t>(after_enter_tx_us - repeat_start_us) : 0;
      const uint32_t actual_gap_from_previous_end_to_first_bit_us =
          previous_repeat_end_us > 0 ? static_cast<uint32_t>(first_bit_us - previous_repeat_end_us) : 0;
      if (burst_first_bit_us == 0) {
        burst_first_bit_us = first_bit_us;
      }

      const uint32_t first_bit_cycle = esp_cpu_get_cycle_count();
      if (!preload_first_bit) {
        this->async_tx_pin_()->digital_write(first_bit_level);
      }

      for (uint32_t schedule_index = 0; schedule_index + 1U < payload_bit_length; schedule_index++) {
        const auto& entry = schedule[schedule_index];
        const uint32_t timing_error_cycles = wait_until_cycle_(first_bit_cycle + entry.time_offset_cycles);
        this->async_tx_pin_()->digital_write(entry.level);
        const uint32_t timing_error_us = timing_error_cycles / cycles_per_us;
        timing.bit_timing_error_min_us = std::min(timing.bit_timing_error_min_us, timing_error_us);
        timing.bit_timing_error_max_us = std::max(timing.bit_timing_error_max_us, timing_error_us);
        timing.bit_timing_error_total_us += timing_error_us;
        timing.bit_timing_samples++;
      }

      const uint32_t end_timing_error_cycles =
          wait_until_cycle_(first_bit_cycle + (cycles_per_bit * payload_bit_length));
      const int64_t repeat_end_us = esp_timer_get_time();
      this->async_tx_pin_()->digital_write(false);
      this->set_idle();
      const uint32_t end_timing_error_us = end_timing_error_cycles / cycles_per_us;
      timing.bit_timing_error_min_us = std::min(timing.bit_timing_error_min_us, end_timing_error_us);
      timing.bit_timing_error_max_us = std::max(timing.bit_timing_error_max_us, end_timing_error_us);
      timing.bit_timing_error_total_us += end_timing_error_us;
      timing.bit_timing_samples++;

      const int64_t previous_repeat_end_for_log_us = previous_repeat_end_us;
      previous_repeat_end_us = repeat_end_us;
      const uint32_t repeat_duration_us = static_cast<uint32_t>(repeat_end_us - first_bit_us);
      timing.min_repeat_duration_us = std::min(timing.min_repeat_duration_us, repeat_duration_us);
      timing.max_repeat_duration_us = std::max(timing.max_repeat_duration_us, repeat_duration_us);
      timing.total_repeat_duration_us += repeat_duration_us;
      timing.inter_repeat_gap_measured_us = actual_gap_from_previous_end_to_first_bit_us;
      timing.total_burst_duration_us = static_cast<uint64_t>(repeat_end_us - burst_first_bit_us);
#if PROFLAME2_TEMBED_TX_DEBUG
      capture_repeat_timing_sample_(timing, transmission_index + 1, repeat_count, repeat_start_us,
                                    previous_repeat_end_for_log_us, first_bit_us, repeat_end_us,
                                    actual_gap_from_previous_end_to_first_bit_us, setup_duration_us, repeat_duration_us,
                                    timing.total_burst_duration_us, this->last_sidled_status_, this->last_sftx_status_,
                                    this->last_stx_status_, MARCSTATE_UNAVAILABLE, MARCSTATE_UNAVAILABLE);
#endif
    }

    // TX timing-critical region ends here.
    elapsed_ms = static_cast<uint32_t>((esp_timer_get_time() - started_us) / 1000);
    error.clear();
    return true;
  }
#endif

  if (tx_mode == TXMode::PROFLAME_NATIVE_GROUPS) {
    std::array<NativePWMGroup, PROFLAME_NATIVE_GROUP_COUNT> groups{};
    size_t group_count = 0;
    size_t trailing_symbol_count = 0;
    std::string native_group_failure_reason;
    if (!decode_native_pwm_groups_(payload, payload_bit_length, groups, group_count, trailing_symbol_count,
                                   &native_group_failure_reason)) {
      if (!native_group_failure_reason.empty()) {
        error = std::string("invalid_native_group_payload:") + native_group_failure_reason;
      } else {
        error = "invalid_native_group_payload";
      }
      return false;
    }

    const NativeGroupTimingProfileSpec native_timing_spec =
        native_group_timing_profile_spec_(native_group_timing_profile, bit_period_us, repeat_gap_us);
    const bool use_native_remote_pcm_row_shaping =
        native_group_timing_profile == NativeGroupTimingProfile::NATIVE_REMOTE;
    const uint32_t expected_native_remote_pcm_bits =
        use_native_remote_pcm_row_shaping ? native_remote_pcm_expected_bits_(length, payload_bit_length) : 0U;
    uint32_t native_remote_pcm_cursor = 0U;
    const uint32_t desired_native_repeat_gap_us = native_timing_spec.desired_repeat_gap_us;
    const uint32_t scheduled_native_repeat_gap_us = native_timing_spec.scheduled_repeat_gap_us;
    timing.repeat_gap_us = scheduled_native_repeat_gap_us;
    const uint32_t cycles_per_us = std::max<uint32_t>(1, esp_rom_get_cpu_ticks_per_us());
    std::array<PWMSymbolScheduleEntry, PWM_SYMBOL_MAX_TRANSITIONS> schedule{};
    std::array<int64_t, PWM_SYMBOL_MAX_REPEAT_DIAGNOSTICS> final_falling_edge_us{};
    std::array<int64_t, PWM_SYMBOL_MAX_REPEAT_DIAGNOSTICS> next_repeat_rising_edge_us{};
    size_t transition_count = 0;
    uint32_t frame_duration_us = 0;
    uint32_t pulse_symbol_count = 0;
    uint32_t sync_symbols_per_repeat = 0;

    for (size_t group_index = 0; group_index < group_count; group_index++) {
      PWMSymbolTiming sync_timing = native_group_symbol_timing_from_spec_(PWMSymbol::SYNC, native_timing_spec, true);
      if (use_native_remote_pcm_row_shaping) {
        uint32_t pcm_advance_bits = 0U;
        std::string diagnostic;
        const bool have_next_symbol = (group_index + 1U < group_count) || groups[group_index].bit_count > 0U;
        choose_native_remote_symbol_timing_from_pcm_row_(payload, length, payload_bit_length, native_timing_spec,
                                                         PWMSymbol::SYNC, pulse_symbol_count, native_remote_pcm_cursor,
                                                         have_next_symbol, sync_timing, pcm_advance_bits, &diagnostic);
        native_remote_pcm_cursor += pcm_advance_bits;
      }
      if (transition_count >= PWM_SYMBOL_MAX_TRANSITIONS) {
        error = "native_group_schedule_overflow";
        return false;
      }
      schedule[transition_count++] = PWMSymbolScheduleEntry{
          .time_offset_cycles = frame_duration_us * cycles_per_us,
          .level = true,
      };
      frame_duration_us += sync_timing.high_us;
      if (transition_count >= PWM_SYMBOL_MAX_TRANSITIONS) {
        error = "native_group_schedule_overflow";
        return false;
      }
      schedule[transition_count++] = PWMSymbolScheduleEntry{
          .time_offset_cycles = frame_duration_us * cycles_per_us,
          .level = false,
      };
      frame_duration_us += sync_timing.low_us;
      pulse_symbol_count++;
      sync_symbols_per_repeat++;

      for (size_t bit_index = 0; bit_index < groups[group_index].bit_count; bit_index++) {
        const PWMSymbol symbol = groups[group_index].bits[bit_index];
        PWMSymbolTiming symbol_timing = native_group_symbol_timing_from_spec_(symbol, native_timing_spec, true);
        if (use_native_remote_pcm_row_shaping) {
          uint32_t pcm_advance_bits = 0U;
          std::string diagnostic;
          const bool have_next_symbol =
              !(group_index + 1U == group_count && bit_index + 1U == groups[group_index].bit_count);
          choose_native_remote_symbol_timing_from_pcm_row_(
              payload, length, payload_bit_length, native_timing_spec, symbol, pulse_symbol_count,
              native_remote_pcm_cursor, have_next_symbol, symbol_timing, pcm_advance_bits, &diagnostic);
          native_remote_pcm_cursor += pcm_advance_bits;
        }
        if (transition_count >= PWM_SYMBOL_MAX_TRANSITIONS) {
          error = "native_group_schedule_overflow";
          return false;
        }
        schedule[transition_count++] = PWMSymbolScheduleEntry{
            .time_offset_cycles = frame_duration_us * cycles_per_us,
            .level = true,
        };
        frame_duration_us += symbol_timing.high_us;
        if (transition_count >= PWM_SYMBOL_MAX_TRANSITIONS) {
          error = "native_group_schedule_overflow";
          return false;
        }
        schedule[transition_count++] = PWMSymbolScheduleEntry{
            .time_offset_cycles = frame_duration_us * cycles_per_us,
            .level = false,
        };
        frame_duration_us += symbol_timing.low_us;
        pulse_symbol_count++;
      }
    }

    if (transition_count == 0) {
      error = "native_group_empty_schedule";
      return false;
    }

    const uint32_t last_falling_edge_offset_us = schedule[transition_count - 1U].time_offset_cycles / cycles_per_us;
    const uint32_t final_symbol_low_us = frame_duration_us - last_falling_edge_offset_us;
    const PWMSymbolTiming desired_native_sync_timing =
        native_group_symbol_timing_from_spec_(PWMSymbol::SYNC, native_timing_spec, false);
    const PWMSymbolTiming desired_native_zero_timing =
        native_group_symbol_timing_from_spec_(PWMSymbol::ZERO, native_timing_spec, false);
    const PWMSymbolTiming desired_native_one_timing =
        native_group_symbol_timing_from_spec_(PWMSymbol::ONE, native_timing_spec, false);

    const bool reenter_tx_between_repeats =
        native_group_repeat_boundary_mode == NativeGroupRepeatBoundaryMode::REENTER_TX;
    TXReadyWaitResult burst_tx_ready_wait{};
    int64_t burst_after_enter_tx_us = 0;
    int64_t burst_tx_ready_us = 0;

    this->async_tx_pin_()->digital_write(false);
    if (!reenter_tx_between_repeats) {
      if (!this->enter_tx_mode_(error)) {
        this->set_idle();
        return false;
      }
      burst_after_enter_tx_us = esp_timer_get_time();
      burst_tx_ready_wait = wait_for_tx_ready_(this, burst_after_enter_tx_us);
      burst_tx_ready_us = burst_after_enter_tx_us + static_cast<int64_t>(burst_tx_ready_wait.settle_wait_us);
    }

    // TX timing-critical region begins here. Native group TX is the production
    // LilyGO waveform path; repeat-gap scheduling and first-edge ordering are
    // RF-visible and must stay hardware-validated.
    for (uint8_t transmission_index = 0; transmission_index < repeat_count; transmission_index++) {
      const int64_t repeat_start_us = esp_timer_get_time();
      TXReadyWaitResult repeat_tx_ready_wait = burst_tx_ready_wait;
      int64_t after_enter_tx_us = burst_after_enter_tx_us;
      int64_t tx_ready_us = burst_tx_ready_us;
      uint32_t setup_before_gap_us = 0;
      uint32_t setup_inside_gap_us = 0;

      if (reenter_tx_between_repeats) {
        if (!this->enter_tx_mode_(error)) {
          this->set_idle();
          return false;
        }
        after_enter_tx_us = esp_timer_get_time();
        repeat_tx_ready_wait = wait_for_tx_ready_(this, after_enter_tx_us);
        tx_ready_us = after_enter_tx_us + static_cast<int64_t>(repeat_tx_ready_wait.settle_wait_us);
        if (tx_ready_us >= repeat_start_us) {
          setup_inside_gap_us = static_cast<uint32_t>(tx_ready_us - repeat_start_us);
        }
      } else {
        setup_before_gap_us = transmission_index == 0 && burst_after_enter_tx_us >= repeat_start_us
                                  ? static_cast<uint32_t>(burst_after_enter_tx_us - repeat_start_us)
                                  : 0;
        setup_inside_gap_us = transmission_index == 0 && burst_tx_ready_us >= burst_after_enter_tx_us
                                  ? static_cast<uint32_t>(burst_tx_ready_us - burst_after_enter_tx_us)
                                  : 0;
      }

      int64_t target_first_rising_edge_us =
          transmission_index == 0 ? tx_ready_us
                                  : previous_repeat_end_us + static_cast<int64_t>(scheduled_native_repeat_gap_us);
      if (transmission_index == 0 && pre_burst_low_us > 0) {
        target_first_rising_edge_us =
            std::max(target_first_rising_edge_us, burst_after_enter_tx_us + static_cast<int64_t>(pre_burst_low_us));
      }
      if (pre_frame_low_us > 0) {
        target_first_rising_edge_us =
            std::max(target_first_rising_edge_us, tx_ready_us + static_cast<int64_t>(pre_frame_low_us));
      }
      if (target_first_rising_edge_us < tx_ready_us) {
        target_first_rising_edge_us = tx_ready_us;
      }
      wait_until_(target_first_rising_edge_us);
      const uint32_t first_symbol_cycle = esp_cpu_get_cycle_count();
      this->async_tx_pin_()->digital_write(true);
      const int64_t first_rising_edge_us = esp_timer_get_time();
      if (transmission_index > 0 && transmission_index - 1U < next_repeat_rising_edge_us.size()) {
        next_repeat_rising_edge_us[transmission_index - 1U] = first_rising_edge_us;
      }
      const uint32_t setup_duration_us = setup_before_gap_us + setup_inside_gap_us;
      const uint32_t measured_rf_visible_repeat_gap_us =
          previous_repeat_end_us > 0 ? static_cast<uint32_t>(first_rising_edge_us - previous_repeat_end_us) : 0;
      const uint32_t first_rising_edge_late_by_us =
          first_rising_edge_us > target_first_rising_edge_us
              ? static_cast<uint32_t>(first_rising_edge_us - target_first_rising_edge_us)
              : 0;
      if (burst_first_bit_us == 0) {
        burst_first_bit_us = first_rising_edge_us;
      }

      int64_t final_fall_us = first_rising_edge_us;
      for (size_t transition_index = 1; transition_index < transition_count; transition_index++) {
        const auto& entry = schedule[transition_index];
        const uint32_t timing_error_cycles = wait_until_cycle_(first_symbol_cycle + entry.time_offset_cycles);
        this->async_tx_pin_()->digital_write(entry.level);
        if (transition_index + 1U == transition_count) {
          final_fall_us = esp_timer_get_time();
        }
        const uint32_t timing_error_us = timing_error_cycles / cycles_per_us;
        timing.bit_timing_error_min_us = std::min(timing.bit_timing_error_min_us, timing_error_us);
        timing.bit_timing_error_max_us = std::max(timing.bit_timing_error_max_us, timing_error_us);
        timing.bit_timing_error_total_us += timing_error_us;
        timing.bit_timing_samples++;
      }

      const uint32_t end_timing_error_cycles =
          wait_until_cycle_(first_symbol_cycle + (frame_duration_us * cycles_per_us));
      if (transmission_index < final_falling_edge_us.size()) {
        final_falling_edge_us[transmission_index] = final_fall_us;
      }
      if (post_frame_idle_gap_us > 0 && transmission_index + 1U == repeat_count) {
        wait_until_cycle_(first_symbol_cycle + ((frame_duration_us + post_frame_idle_gap_us) * cycles_per_us));
      }
      const int64_t repeat_end_us = esp_timer_get_time();
      this->async_tx_pin_()->digital_write(false);
      if (reenter_tx_between_repeats) {
        this->set_idle();
      }
      const uint32_t end_timing_error_us = end_timing_error_cycles / cycles_per_us;
      timing.bit_timing_error_min_us = std::min(timing.bit_timing_error_min_us, end_timing_error_us);
      timing.bit_timing_error_max_us = std::max(timing.bit_timing_error_max_us, end_timing_error_us);
      timing.bit_timing_error_total_us += end_timing_error_us;
      timing.bit_timing_samples++;

      const int64_t previous_repeat_end_for_log_us = previous_repeat_end_us;
      previous_repeat_end_us = final_fall_us;
      const uint32_t repeat_duration_us = static_cast<uint32_t>(repeat_end_us - first_rising_edge_us);
      timing.min_repeat_duration_us = std::min(timing.min_repeat_duration_us, repeat_duration_us);
      timing.max_repeat_duration_us = std::max(timing.max_repeat_duration_us, repeat_duration_us);
      timing.total_repeat_duration_us += repeat_duration_us;
      timing.inter_repeat_gap_measured_us = measured_rf_visible_repeat_gap_us;
      if (transmission_index > 0) {
        timing.inter_repeat_gap_min_us = std::min(timing.inter_repeat_gap_min_us, measured_rf_visible_repeat_gap_us);
        timing.inter_repeat_gap_max_us = std::max(timing.inter_repeat_gap_max_us, measured_rf_visible_repeat_gap_us);
        timing.inter_repeat_gap_total_us += measured_rf_visible_repeat_gap_us;
        timing.inter_repeat_gap_samples++;
        timing.first_rising_edge_late_min_us =
            std::min(timing.first_rising_edge_late_min_us, first_rising_edge_late_by_us);
        timing.first_rising_edge_late_max_us =
            std::max(timing.first_rising_edge_late_max_us, first_rising_edge_late_by_us);
        timing.first_rising_edge_late_total_us += first_rising_edge_late_by_us;
        timing.first_rising_edge_late_samples++;
      }
      timing.total_burst_duration_us = static_cast<uint64_t>(repeat_end_us - burst_first_bit_us);
#if PROFLAME2_TEMBED_TX_DEBUG
      capture_repeat_timing_sample_(timing, transmission_index + 1, repeat_count, repeat_start_us,
                                    previous_repeat_end_for_log_us, first_rising_edge_us, repeat_end_us,
                                    measured_rf_visible_repeat_gap_us, setup_duration_us, repeat_duration_us,
                                    timing.total_burst_duration_us, this->last_sidled_status_, this->last_sftx_status_,
                                    this->last_stx_status_, MARCSTATE_UNAVAILABLE, MARCSTATE_UNAVAILABLE);
#endif
    }

    this->async_tx_pin_()->digital_write(false);
    this->set_idle();

    // TX timing-critical region ends here.
    elapsed_ms = static_cast<uint32_t>((esp_timer_get_time() - started_us) / 1000);
    error.clear();
    return true;
  }

  if (tx_mode == TXMode::PROFLAME_PWM_SYMBOLS) {
    std::array<PWMSymbol, PWM_SYMBOL_MAX_SYMBOLS> symbols{};
    size_t symbol_count = 0;
    if (!decode_pwm_symbols_(payload, payload_bit_length, symbols, symbol_count)) {
      error = "invalid_pwm_symbol_payload";
      return false;
    }

    const uint32_t cycles_per_us = std::max<uint32_t>(1, esp_rom_get_cpu_ticks_per_us());
    std::array<PWMSymbolScheduleEntry, PWM_SYMBOL_MAX_TRANSITIONS> schedule{};
    std::array<int64_t, PWM_SYMBOL_MAX_REPEAT_DIAGNOSTICS> final_falling_edge_us{};
    std::array<int64_t, PWM_SYMBOL_MAX_REPEAT_DIAGNOSTICS> next_repeat_rising_edge_us{};
    size_t transition_count = 0;
    uint32_t frame_duration_us = 0;
    bool first_symbol_drives_high = false;

    for (size_t symbol_index = 0; symbol_index < symbol_count; symbol_index++) {
      const PWMSymbolTiming symbol_timing = pwm_symbol_timing_(symbols[symbol_index], bit_period_us);
      const bool emits_high = symbol_timing.high_us > 0U;
      if (symbol_index == 0) {
        first_symbol_drives_high = emits_high;
      } else if (emits_high) {
        if (transition_count >= PWM_SYMBOL_MAX_TRANSITIONS) {
          error = "pwm_symbol_schedule_overflow";
          return false;
        }
        schedule[transition_count++] = PWMSymbolScheduleEntry{
            .time_offset_cycles = frame_duration_us * cycles_per_us,
            .level = true,
        };
      }
      if (emits_high) {
        frame_duration_us += symbol_timing.high_us;
        if (transition_count >= PWM_SYMBOL_MAX_TRANSITIONS) {
          error = "pwm_symbol_schedule_overflow";
          return false;
        }
        schedule[transition_count++] = PWMSymbolScheduleEntry{
            .time_offset_cycles = frame_duration_us * cycles_per_us,
            .level = false,
        };
      }
      frame_duration_us += symbol_timing.low_us;
    }

    const int64_t burst_setup_start_us = esp_timer_get_time();
    if (first_symbol_drives_high) {
      this->async_tx_pin_()->digital_write(true);
    } else {
      this->async_tx_pin_()->digital_write(false);
    }
    if (!this->enter_tx_mode_(error)) {
      this->set_idle();
      return false;
    }
    const int64_t after_enter_tx_us = esp_timer_get_time();
    const TXReadyWaitResult tx_ready_wait = wait_for_tx_ready_(this, after_enter_tx_us);
    const int64_t tx_ready_us = after_enter_tx_us + static_cast<int64_t>(tx_ready_wait.settle_wait_us);
    // TX timing-critical region begins here. This PWM-symbol path uses
    // precomputed transitions, but the emission loop still owns RF-visible
    // wait/pin-write ordering.
    for (uint8_t transmission_index = 0; transmission_index < repeat_count; transmission_index++) {
      const int64_t repeat_start_us = esp_timer_get_time();
      int64_t desired_first_symbol_us = transmission_index == 0 ? tx_ready_us : previous_repeat_end_us;
      if (transmission_index == 0) {
        if (desired_first_symbol_us < tx_ready_us) {
          desired_first_symbol_us = tx_ready_us;
        }
      } else {
        desired_first_symbol_us += static_cast<int64_t>(repeat_gap_us);
      }

      if (!first_symbol_drives_high) {
        this->async_tx_pin_()->digital_write(false);
      }
      wait_until_(desired_first_symbol_us);
      const int64_t first_symbol_us = esp_timer_get_time();
      if (transmission_index > 0 && transmission_index - 1U < next_repeat_rising_edge_us.size()) {
        next_repeat_rising_edge_us[transmission_index - 1U] = first_symbol_us;
      }
      const uint32_t setup_duration_us = transmission_index == 0 && after_enter_tx_us >= burst_setup_start_us
                                             ? static_cast<uint32_t>(after_enter_tx_us - burst_setup_start_us)
                                             : 0;
      const uint32_t actual_gap_from_previous_end_to_first_bit_us =
          previous_repeat_end_us > 0 ? static_cast<uint32_t>(first_symbol_us - previous_repeat_end_us) : 0;
      if (burst_first_bit_us == 0) {
        burst_first_bit_us = first_symbol_us;
      }

      const uint32_t first_symbol_cycle = esp_cpu_get_cycle_count();
      for (size_t transition_index = 0; transition_index < transition_count; transition_index++) {
        const auto& entry = schedule[transition_index];
        const uint32_t timing_error_cycles = wait_until_cycle_(first_symbol_cycle + entry.time_offset_cycles);
        this->async_tx_pin_()->digital_write(entry.level);
        const uint32_t timing_error_us = timing_error_cycles / cycles_per_us;
        timing.bit_timing_error_min_us = std::min(timing.bit_timing_error_min_us, timing_error_us);
        timing.bit_timing_error_max_us = std::max(timing.bit_timing_error_max_us, timing_error_us);
        timing.bit_timing_error_total_us += timing_error_us;
        timing.bit_timing_samples++;
      }

      const uint32_t end_timing_error_cycles =
          wait_until_cycle_(first_symbol_cycle + (frame_duration_us * cycles_per_us));
      const int64_t final_fall_us = esp_timer_get_time();
      this->async_tx_pin_()->digital_write(false);
      if (transmission_index < final_falling_edge_us.size()) {
        final_falling_edge_us[transmission_index] = final_fall_us;
      }
      if (post_frame_idle_gap_us > 0 && transmission_index + 1U == repeat_count) {
        wait_until_cycle_(first_symbol_cycle + ((frame_duration_us + post_frame_idle_gap_us) * cycles_per_us));
      }
      const int64_t repeat_end_us = esp_timer_get_time();
      const uint32_t end_timing_error_us = end_timing_error_cycles / cycles_per_us;
      timing.bit_timing_error_min_us = std::min(timing.bit_timing_error_min_us, end_timing_error_us);
      timing.bit_timing_error_max_us = std::max(timing.bit_timing_error_max_us, end_timing_error_us);
      timing.bit_timing_error_total_us += end_timing_error_us;
      timing.bit_timing_samples++;

      const int64_t previous_repeat_end_for_log_us = previous_repeat_end_us;
      previous_repeat_end_us = final_fall_us;
      const uint32_t repeat_duration_us = static_cast<uint32_t>(repeat_end_us - first_symbol_us);
      timing.min_repeat_duration_us = std::min(timing.min_repeat_duration_us, repeat_duration_us);
      timing.max_repeat_duration_us = std::max(timing.max_repeat_duration_us, repeat_duration_us);
      timing.total_repeat_duration_us += repeat_duration_us;
      timing.inter_repeat_gap_measured_us = actual_gap_from_previous_end_to_first_bit_us;
      timing.total_burst_duration_us = static_cast<uint64_t>(repeat_end_us - burst_first_bit_us);
#if PROFLAME2_TEMBED_TX_DEBUG
      capture_repeat_timing_sample_(timing, transmission_index + 1, repeat_count, repeat_start_us,
                                    previous_repeat_end_for_log_us, first_symbol_us, repeat_end_us,
                                    actual_gap_from_previous_end_to_first_bit_us, setup_duration_us, repeat_duration_us,
                                    timing.total_burst_duration_us, this->last_sidled_status_, this->last_sftx_status_,
                                    this->last_stx_status_, MARCSTATE_UNAVAILABLE, MARCSTATE_UNAVAILABLE);
#endif
    }
    this->set_idle();

    // TX timing-critical region ends here.
    elapsed_ms = static_cast<uint32_t>((esp_timer_get_time() - started_us) / 1000);
    error.clear();
    return true;
  }

  if (tx_mode == TXMode::CONTINUOUS_BURST) {
    const bool preload_first_bit = (pre_burst_low_us == 0);
    const bool first_bit_level = payload_bit_at_(payload, 0);
    if (preload_first_bit) {
      this->async_tx_pin_()->digital_write(first_bit_level);
    } else {
      this->async_tx_pin_()->digital_write(false);
    }
    if (!this->enter_tx_mode_(error)) {
      this->set_idle();
      return false;
    }
    const int64_t after_enter_tx_us = esp_timer_get_time();
    const TXReadyWaitResult tx_ready_wait = wait_for_tx_ready_(this, after_enter_tx_us);

    // TX timing-critical region begins here. Do not log, allocate, publish state,
    // read SPI registers, or perform other potentially blocking work until the
    // radio has returned to a safe non-timing-critical state.
    for (uint8_t transmission_index = 0; transmission_index < repeat_count; transmission_index++) {
      const int64_t repeat_start_us = esp_timer_get_time();
      int64_t desired_first_bit_us = after_enter_tx_us;
      if (transmission_index > 0 && previous_repeat_end_us > 0) {
        desired_first_bit_us = previous_repeat_end_us + static_cast<int64_t>(repeat_gap_us);
      }
      if (transmission_index == 0 && pre_burst_low_us > 0) {
        desired_first_bit_us =
            std::max(desired_first_bit_us, after_enter_tx_us + static_cast<int64_t>(pre_burst_low_us));
      }
      const int64_t tx_ready_us = after_enter_tx_us + static_cast<int64_t>(tx_ready_wait.settle_wait_us);
      if (desired_first_bit_us < tx_ready_us) {
        desired_first_bit_us = tx_ready_us;
      }
      if (!preload_first_bit) {
        this->async_tx_pin_()->digital_write(false);
      }
      wait_until_(desired_first_bit_us);
      const int64_t first_bit_us = esp_timer_get_time();
      const uint32_t setup_duration_us = transmission_index == 0 && after_enter_tx_us >= repeat_start_us
                                             ? static_cast<uint32_t>(after_enter_tx_us - repeat_start_us)
                                             : 0;
      const uint32_t actual_gap_from_previous_end_to_first_bit_us =
          previous_repeat_end_us > 0 ? static_cast<uint32_t>(first_bit_us - previous_repeat_end_us) : 0;
      if (burst_first_bit_us == 0) {
        burst_first_bit_us = first_bit_us;
      }
      uint64_t timing_step_index = 0;

      for (uint32_t payload_bit_index = 0; payload_bit_index < payload_bit_length; payload_bit_index++) {
        const bool high = payload_bit_at_(payload, payload_bit_index);
        if (payload_bit_index == 0) {
          if (!preload_first_bit) {
            this->async_tx_pin_()->digital_write(high);
          }
        } else {
          this->async_tx_pin_()->digital_write(high);
        }
        timing_step_index++;
        const uint32_t timing_error_us =
            wait_until_(first_bit_us + schedule_offset_us_(timing_step_index, this->data_rate_bps_));
#if PROFLAME2_TEMBED_TX_DEBUG
        if (transmission_index == 0 &&
            timing.bit_timing_trace_count < TXTimingDiagnostics::BIT_TIMING_SAMPLE_CAPACITY) {
          const int64_t actual_offset_us = esp_timer_get_time() - first_bit_us;
          const int64_t target_offset_us = schedule_offset_us_(timing_step_index, this->data_rate_bps_);
          capture_bit_timing_sample_(timing, payload_bit_index, high, target_offset_us, actual_offset_us,
                                     timing_error_us);
        }
#endif
        timing.bit_timing_error_min_us = std::min(timing.bit_timing_error_min_us, timing_error_us);
        timing.bit_timing_error_max_us = std::max(timing.bit_timing_error_max_us, timing_error_us);
        timing.bit_timing_error_total_us += timing_error_us;
        timing.bit_timing_samples++;
      }

      this->async_tx_pin_()->digital_write(false);
      const int64_t repeat_end_us = esp_timer_get_time();
      const int64_t previous_repeat_end_for_log_us = previous_repeat_end_us;
      previous_repeat_end_us = repeat_end_us;
      const uint32_t repeat_duration_us = static_cast<uint32_t>(repeat_end_us - first_bit_us);
      timing.min_repeat_duration_us = std::min(timing.min_repeat_duration_us, repeat_duration_us);
      timing.max_repeat_duration_us = std::max(timing.max_repeat_duration_us, repeat_duration_us);
      timing.total_repeat_duration_us += repeat_duration_us;
      timing.inter_repeat_gap_measured_us = actual_gap_from_previous_end_to_first_bit_us;
      timing.total_burst_duration_us = static_cast<uint64_t>(repeat_end_us - burst_first_bit_us);
#if PROFLAME2_TEMBED_TX_DEBUG
      capture_repeat_timing_sample_(timing, transmission_index + 1, repeat_count, repeat_start_us,
                                    previous_repeat_end_for_log_us, first_bit_us, repeat_end_us,
                                    actual_gap_from_previous_end_to_first_bit_us, setup_duration_us, repeat_duration_us,
                                    timing.total_burst_duration_us, this->last_sidled_status_, this->last_sftx_status_,
                                    this->last_stx_status_, MARCSTATE_UNAVAILABLE, MARCSTATE_UNAVAILABLE);
#endif
    }

    this->set_idle();
    // TX timing-critical region ends here.
    elapsed_ms = static_cast<uint32_t>((esp_timer_get_time() - started_us) / 1000);
    error.clear();
    return true;
  }

  // TX timing-critical region begins here. Do not log, allocate, publish state,
  // read SPI registers, or perform other potentially blocking work until the
  // radio has returned to a safe non-timing-critical state.
  for (uint8_t transmission_index = 0; transmission_index < repeat_count; transmission_index++) {
    const int64_t repeat_start_us = esp_timer_get_time();
    int64_t desired_first_bit_us = repeat_start_us;
    if (transmission_index > 0 && previous_repeat_end_us > 0) {
      desired_first_bit_us = previous_repeat_end_us + static_cast<int64_t>(repeat_gap_us);
    }
    const bool preload_first_bit = (pre_frame_low_us == 0 && repeat_gap_us == 0);
    const bool first_bit_level = payload_bit_at_(payload, 0);
    if (preload_first_bit) {
      this->async_tx_pin_()->digital_write(first_bit_level);
    } else {
      this->async_tx_pin_()->digital_write(false);
    }
    if (!this->enter_tx_mode_(error)) {
      this->set_idle();
      return false;
    }
    const int64_t after_enter_tx_us = esp_timer_get_time();
    const TXReadyWaitResult tx_ready_wait = wait_for_tx_ready_(this, after_enter_tx_us);
    const int64_t tx_ready_us = after_enter_tx_us + static_cast<int64_t>(tx_ready_wait.settle_wait_us);
    if (desired_first_bit_us < tx_ready_us) {
      desired_first_bit_us = tx_ready_us;
    }
    if (pre_frame_low_us > 0) {
      desired_first_bit_us = std::max(desired_first_bit_us, after_enter_tx_us + static_cast<int64_t>(pre_frame_low_us));
    }
    if (!preload_first_bit) {
      this->async_tx_pin_()->digital_write(false);
    }
    wait_until_(desired_first_bit_us);
    const int64_t first_bit_us = esp_timer_get_time();
    const uint32_t setup_duration_us =
        after_enter_tx_us >= repeat_start_us ? static_cast<uint32_t>(after_enter_tx_us - repeat_start_us) : 0;
    const uint32_t actual_gap_from_previous_end_to_first_bit_us =
        previous_repeat_end_us > 0 ? static_cast<uint32_t>(first_bit_us - previous_repeat_end_us) : 0;
    if (burst_first_bit_us == 0) {
      burst_first_bit_us = first_bit_us;
    }
    uint64_t timing_step_index = 0;

    for (uint32_t payload_bit_index = 0; payload_bit_index < payload_bit_length; payload_bit_index++) {
      const bool high = payload_bit_at_(payload, payload_bit_index);
      if (payload_bit_index == 0) {
        if (!preload_first_bit) {
          this->async_tx_pin_()->digital_write(high);
        }
      } else {
        this->async_tx_pin_()->digital_write(high);
      }
      timing_step_index++;
      const uint32_t timing_error_us =
          wait_until_(first_bit_us + schedule_offset_us_(timing_step_index, this->data_rate_bps_));
#if PROFLAME2_TEMBED_TX_DEBUG
      if (transmission_index == 0 && timing.bit_timing_trace_count < TXTimingDiagnostics::BIT_TIMING_SAMPLE_CAPACITY) {
        const int64_t actual_offset_us = esp_timer_get_time() - first_bit_us;
        const int64_t target_offset_us = schedule_offset_us_(timing_step_index, this->data_rate_bps_);
        capture_bit_timing_sample_(timing, payload_bit_index, high, target_offset_us, actual_offset_us,
                                   timing_error_us);
      }
#endif
      timing.bit_timing_error_min_us = std::min(timing.bit_timing_error_min_us, timing_error_us);
      timing.bit_timing_error_max_us = std::max(timing.bit_timing_error_max_us, timing_error_us);
      timing.bit_timing_error_total_us += timing_error_us;
      timing.bit_timing_samples++;
    }

    const int64_t repeat_end_us = esp_timer_get_time();
    this->async_tx_pin_()->digital_write(false);
    this->set_idle();
    const int64_t previous_repeat_end_for_log_us = previous_repeat_end_us;
    previous_repeat_end_us = repeat_end_us;
    const uint32_t repeat_duration_us = static_cast<uint32_t>(repeat_end_us - first_bit_us);
    timing.min_repeat_duration_us = std::min(timing.min_repeat_duration_us, repeat_duration_us);
    timing.max_repeat_duration_us = std::max(timing.max_repeat_duration_us, repeat_duration_us);
    timing.total_repeat_duration_us += repeat_duration_us;
    timing.inter_repeat_gap_measured_us = actual_gap_from_previous_end_to_first_bit_us;
    timing.total_burst_duration_us = static_cast<uint64_t>(repeat_end_us - burst_first_bit_us);
#if PROFLAME2_TEMBED_TX_DEBUG
    capture_repeat_timing_sample_(timing, transmission_index + 1, repeat_count, repeat_start_us,
                                  previous_repeat_end_for_log_us, first_bit_us, repeat_end_us,
                                  actual_gap_from_previous_end_to_first_bit_us, setup_duration_us, repeat_duration_us,
                                  timing.total_burst_duration_us, this->last_sidled_status_, this->last_sftx_status_,
                                  this->last_stx_status_, MARCSTATE_UNAVAILABLE, MARCSTATE_UNAVAILABLE);
#endif
  }

  // TX timing-critical region ends here.
  elapsed_ms = static_cast<uint32_t>((esp_timer_get_time() - started_us) / 1000);
  error.clear();
  return true;
}

bool RadioCC1101::transmit_test_pattern_async_ook(TestPatternMode mode, uint32_t duration_ms, uint32_t period_us,
                                                  uint32_t& elapsed_ms, TXTimingDiagnostics& timing,
                                                  std::string& error) {
  elapsed_ms = 0;
  timing = TXTimingDiagnostics{};
  if (!this->initialized_) {
    error = "radio_not_initialized";
    return false;
  }
  if (this->data_rate_bps_ == 0) {
    error = "invalid_data_rate";
    return false;
  }

  const uint32_t bit_period_us =
      std::max<uint32_t>(1, static_cast<uint32_t>((1000000ULL + (this->data_rate_bps_ / 2U)) / this->data_rate_bps_));
  timing.bit_period_us = bit_period_us;
  const int64_t started_us = esp_timer_get_time();
  const int64_t deadline_us = started_us + static_cast<int64_t>(duration_ms) * 1000LL;
  if (!this->enter_tx_mode_(error)) {
    this->set_idle();
    return false;
  }

  if (mode == TestPatternMode::CARRIER_ON) {
    this->async_tx_pin_()->digital_write(true);
    wait_until_(deadline_us);
  } else if (mode == TestPatternMode::CARRIER_OFF) {
    this->async_tx_pin_()->digital_write(false);
    wait_until_(deadline_us);
  } else {
    const uint32_t effective_period_us = std::max<uint32_t>(100, period_us);
    bool high = false;
    uint64_t timing_step_index = 0;
    while ((started_us + static_cast<int64_t>(effective_period_us) * static_cast<int64_t>(timing_step_index + 1)) <
           deadline_us) {
      high = !high;
      this->async_tx_pin_()->digital_write(high);
      timing.payload_bits++;
      timing_step_index++;
      const int64_t target_us =
          started_us + static_cast<int64_t>(effective_period_us) * static_cast<int64_t>(timing_step_index);
      const uint32_t timing_error_us = wait_until_(target_us);
      timing.bit_timing_error_min_us = std::min(timing.bit_timing_error_min_us, timing_error_us);
      timing.bit_timing_error_max_us = std::max(timing.bit_timing_error_max_us, timing_error_us);
      timing.bit_timing_error_total_us += timing_error_us;
      timing.bit_timing_samples++;
    }
  }

  this->async_tx_pin_()->digital_write(false);
  const uint8_t marcstate_after_pattern = this->read_marcstate();
  this->set_idle();
  const uint8_t marcstate_after_idle = this->read_marcstate();
  elapsed_ms = static_cast<uint32_t>((esp_timer_get_time() - started_us) / 1000);
  ESP_LOGI(TAG,
           "Test pattern complete elapsed_ms=%" PRIu32
           " marcstate_after_pattern=0x%02X(%s) marcstate_after_idle=0x%02X(%s)",
           elapsed_ms, marcstate_after_pattern, marcstate_to_string_(marcstate_after_pattern), marcstate_after_idle,
           marcstate_to_string_(marcstate_after_idle));
  error.clear();
  return true;
}

uint32_t RadioCC1101::wait_until_(int64_t target_us) {
  while (true) {
    const int64_t now_us = esp_timer_get_time();
    if (now_us >= target_us) {
      return static_cast<uint32_t>(now_us - target_us);
    }
    const int64_t remaining_us = target_us - now_us;
    if (remaining_us > 200) {
      esp_rom_delay_us(static_cast<uint32_t>(remaining_us - 100));
      continue;
    }
  }
}

#if PROFLAME2_TEMBED_TX_DEBUG
void RadioCC1101::capture_first_bits_(TXTimingDiagnostics& timing, const uint8_t* payload,
                                      uint32_t payload_bit_length) {
  if (payload == nullptr || payload_bit_length == 0) {
    return;
  }
  const uint32_t bit_count = std::min<uint32_t>(16, payload_bit_length);
  timing.first_bits.fill('\0');
  for (uint32_t i = 0; i < bit_count; i++) {
    const size_t byte_index = i / 8U;
    const int bit_index = 7 - static_cast<int>(i % 8U);
    timing.first_bits[i] = ((payload[byte_index] >> bit_index) & 0x01U) != 0 ? '1' : '0';
  }
  timing.first_bits[bit_count] = '\0';
  timing.first_bits_count = static_cast<uint8_t>(bit_count);
}

void RadioCC1101::log_bit_timing_trace_(const TXTimingDiagnostics& timing) {
  if (timing.bit_timing_trace_count == 0) {
    return;
  }
  ESP_LOGI(TAG, "TX bit_timing_trace samples=%u", timing.bit_timing_trace_count);
  for (uint8_t i = 0; i < timing.bit_timing_trace_count; i++) {
    const auto& sample = timing.bit_timing_trace[i];
    ESP_LOGI(
        TAG, "TX bit[%u] value=%u target_offset_us=%" PRId64 " actual_offset_us=%" PRId64 " timing_error_us=%" PRIu32,
        sample.bit_index, sample.bit_value, sample.target_offset_us, sample.actual_offset_us, sample.timing_error_us);
  }
}

void RadioCC1101::log_repeat_timing_trace_(const TXTimingDiagnostics& timing, TXMode tx_mode) {
  if (timing.repeat_timing_trace_count == 0) {
    return;
  }
  ESP_LOGI(TAG, "TX repeat_timing_trace samples=%u tx_mode=%s", timing.repeat_timing_trace_count,
           tx_mode_to_string_(tx_mode));
  for (uint8_t i = 0; i < timing.repeat_timing_trace_count; i++) {
    const auto& sample = timing.repeat_timing_trace[i];
    const char* marcstate_after_repeat_text = sample.marcstate_after_repeat == MARCSTATE_UNAVAILABLE
                                                  ? "n/a"
                                                  : marcstate_to_string_(sample.marcstate_after_repeat);
    ESP_LOGI(TAG,
             "TX repeat=%u/%u complete repeat_start_us=%" PRId64 " previous_repeat_end_us=%" PRId64
             " first_bit_us=%" PRId64 " actual_gap_from_previous_end_to_first_bit_us=%" PRIu32
             " setup_duration_before_first_bit_us=%" PRIu32 " strobe_status sidle=0x%02X sftx=0x%02X stx=0x%02X "
             "marcstate_after_enter_tx=0x%02X(%s) marcstate_after_repeat=0x%02X(%s) "
             "repeat_end_us=%" PRId64 " frame_duration_us=%" PRIu32 " total_burst_duration_us=%" PRIu64 " tx_mode=%s",
             sample.repeat_index, sample.repeat_count, sample.repeat_start_us, sample.previous_repeat_end_us,
             sample.first_bit_us, sample.actual_gap_from_previous_end_to_first_bit_us,
             sample.setup_duration_before_first_bit_us, sample.strobe_sidle_status, sample.strobe_sftx_status,
             sample.strobe_stx_status, sample.marcstate_after_enter_tx,
             marcstate_to_string_(sample.marcstate_after_enter_tx), sample.marcstate_after_repeat,
             marcstate_after_repeat_text, sample.repeat_end_us, sample.frame_duration_us,
             sample.total_burst_duration_us, tx_mode_to_string_(tx_mode));
  }
}

bool RadioCC1101::drain_debug_tx_diagnostics(const TXTimingDiagnostics& timing, TXMode tx_mode, uint8_t& phase,
                                             uint8_t& repeat_index, uint8_t& bit_index) const {
  switch (phase) {
  case 0:
    phase = 1;
    if (timing.first_bits_count > 0) {
      ESP_LOGI(TAG, "TX first_bits[%u]=%s", timing.first_bits_count, timing.first_bits.data());
      return true;
    }
    [[fallthrough]];
  case 1:
    phase = 2;
    if (timing.repeat_timing_trace_count > 0) {
      ESP_LOGI(TAG, "TX repeat_timing_trace samples=%u tx_mode=%s", timing.repeat_timing_trace_count,
               tx_mode_to_string_(tx_mode));
      return true;
    }
    [[fallthrough]];
  case 2:
    if (repeat_index < timing.repeat_timing_trace_count) {
      const auto& sample = timing.repeat_timing_trace[repeat_index++];
      const char* marcstate_after_repeat_text = sample.marcstate_after_repeat == MARCSTATE_UNAVAILABLE
                                                    ? "n/a"
                                                    : marcstate_to_string_(sample.marcstate_after_repeat);
      ESP_LOGI(TAG,
               "TX repeat=%u/%u complete repeat_start_us=%" PRId64 " previous_repeat_end_us=%" PRId64
               " first_bit_us=%" PRId64 " actual_gap_from_previous_end_to_first_bit_us=%" PRIu32
               " setup_duration_before_first_bit_us=%" PRIu32 " strobe_status sidle=0x%02X sftx=0x%02X stx=0x%02X "
               "marcstate_after_enter_tx=0x%02X(%s) marcstate_after_repeat=0x%02X(%s) "
               "repeat_end_us=%" PRId64 " frame_duration_us=%" PRIu32 " total_burst_duration_us=%" PRIu64 " tx_mode=%s",
               sample.repeat_index, sample.repeat_count, sample.repeat_start_us, sample.previous_repeat_end_us,
               sample.first_bit_us, sample.actual_gap_from_previous_end_to_first_bit_us,
               sample.setup_duration_before_first_bit_us, sample.strobe_sidle_status, sample.strobe_sftx_status,
               sample.strobe_stx_status, sample.marcstate_after_enter_tx,
               marcstate_to_string_(sample.marcstate_after_enter_tx), sample.marcstate_after_repeat,
               marcstate_after_repeat_text, sample.repeat_end_us, sample.frame_duration_us,
               sample.total_burst_duration_us, tx_mode_to_string_(tx_mode));
      return true;
    }
    phase = 3;
    [[fallthrough]];
  case 3:
    phase = 4;
    if (timing.bit_timing_trace_count > 0) {
      ESP_LOGI(TAG, "TX bit_timing_trace samples=%u", timing.bit_timing_trace_count);
      return true;
    }
    [[fallthrough]];
  case 4:
    if (bit_index < timing.bit_timing_trace_count) {
      const auto& sample = timing.bit_timing_trace[bit_index++];
      ESP_LOGI(
          TAG, "TX bit[%u] value=%u target_offset_us=%" PRId64 " actual_offset_us=%" PRId64 " timing_error_us=%" PRIu32,
          sample.bit_index, sample.bit_value, sample.target_offset_us, sample.actual_offset_us, sample.timing_error_us);
      return true;
    }
    phase = 5;
    [[fallthrough]];
  default:
    return false;
  }
}
#else
bool RadioCC1101::drain_debug_tx_diagnostics(const TXTimingDiagnostics& timing, TXMode tx_mode, uint8_t& phase,
                                             uint8_t& repeat_index, uint8_t& bit_index) const {
  (void)timing;
  (void)tx_mode;
  (void)phase;
  (void)repeat_index;
  (void)bit_index;
  return false;
}
#endif

} // namespace proflame2_tembed
} // namespace esphome
