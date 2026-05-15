#include "proflame2_decoder.h"

#include <array>

namespace esphome {
namespace proflame2_tembed {

namespace {

constexpr uint8_t PROFLAME_WORD_COUNT = 7;
constexpr uint8_t SYMBOLS_PER_WORD = 13;
constexpr uint8_t TRAILING_ZERO_SYMBOLS = 9;
constexpr uint16_t TOTAL_SYMBOLS = (PROFLAME_WORD_COUNT * SYMBOLS_PER_WORD) + TRAILING_ZERO_SYMBOLS;
constexpr uint16_t TOTAL_AIR_BITS = TOTAL_SYMBOLS * 2;
constexpr uint8_t MAX_OWNER_CANDIDATES = 8;

struct CandidateFrame {
  bool used{false};
  uint32_t serial_id{0};
  uint8_t cmd1{0};
  uint8_t err1{0};
  uint8_t cmd2{0};
  uint8_t err2{0};
  uint8_t repeat_count{0};
  uint8_t bit_offset{0};
  uint16_t symbol_offset{0};
  uint32_t absolute_bit_offset{0};
};

bool bit_at(const uint8_t* payload, size_t length, uint32_t bit_index) {
  const uint32_t byte_index = bit_index / 8U;
  if (byte_index >= length) {
    return false;
  }
  const uint8_t shift = static_cast<uint8_t>(7U - (bit_index % 8U));
  return ((payload[byte_index] >> shift) & 0x01U) != 0U;
}

char symbol_at(const uint8_t* payload, size_t length, uint32_t bit_index) {
  const bool first = bit_at(payload, length, bit_index);
  const bool second = bit_at(payload, length, bit_index + 1U);
  if (first && second) {
    return 'S';
  }
  if (!first && second) {
    return '0';
  }
  if (first && !second) {
    return '1';
  }
  return 'Z';
}

bool parse_word_symbol(char symbol, uint16_t* word_value, uint8_t* one_count) {
  if (symbol != '0' && symbol != '1') {
    return false;
  }
  *word_value = static_cast<uint16_t>((*word_value << 1U) | (symbol == '1' ? 1U : 0U));
  if (symbol == '1') {
    (*one_count)++;
  }
  return true;
}

bool parse_symbol_window(const uint8_t* payload, size_t length, uint8_t bit_offset, uint16_t symbol_offset,
                         CandidateFrame* frame, const char** reject_reason) {
  std::array<uint16_t, PROFLAME_WORD_COUNT> words{};
  for (uint8_t word_index = 0; word_index < PROFLAME_WORD_COUNT; word_index++) {
    const uint16_t word_symbol_offset = static_cast<uint16_t>(symbol_offset + (word_index * SYMBOLS_PER_WORD));
    const uint32_t word_bit_offset =
        static_cast<uint32_t>(bit_offset) + (static_cast<uint32_t>(word_symbol_offset) * 2U);
    if (symbol_at(payload, length, word_bit_offset) != 'S' || symbol_at(payload, length, word_bit_offset + 2U) != '1' ||
        symbol_at(payload, length, word_bit_offset + 24U) != '1') {
      *reject_reason = "bad_start_end_guard";
      return false;
    }

    uint16_t word_value = 0U;
    uint8_t one_count = 0U;
    for (uint8_t bit = 0; bit < 9; bit++) {
      if (!parse_word_symbol(symbol_at(payload, length, word_bit_offset + 4U + (bit * 2U)), &word_value, &one_count)) {
        *reject_reason = "invalid_word_symbol";
        return false;
      }
    }

    const char parity_symbol = symbol_at(payload, length, word_bit_offset + 22U);
    if (parity_symbol != '0' && parity_symbol != '1') {
      *reject_reason = "invalid_parity_symbol";
      return false;
    }
    if ((parity_symbol == '1' ? 1U : 0U) != (one_count % 2U)) {
      *reject_reason = "bad_parity";
      return false;
    }
    words[word_index] = word_value;
  }

  uint8_t leading_zero_guard_symbols = 0U;
  const uint16_t trailer_offset = symbol_offset + (PROFLAME_WORD_COUNT * SYMBOLS_PER_WORD);
  for (uint8_t index = 0; index < TRAILING_ZERO_SYMBOLS; index++) {
    const uint32_t bit_index = static_cast<uint32_t>(bit_offset) + (static_cast<uint32_t>(trailer_offset + index) * 2U);
    if (symbol_at(payload, length, bit_index) != 'Z') {
      break;
    }
    leading_zero_guard_symbols++;
  }
  if (leading_zero_guard_symbols < 4U) {
    *reject_reason = "bad_trailing_zero_guard";
    return false;
  }

  if ((words[0] & 0x01U) != 1U || (words[1] & 0x01U) != 0U || (words[2] & 0x01U) != 0U) {
    *reject_reason = "bad_serial_word_layout";
    return false;
  }
  for (uint8_t word_index = 3; word_index < PROFLAME_WORD_COUNT; word_index++) {
    if ((words[word_index] & 0x01U) != 0U) {
      *reject_reason = "bad_command_word_layout";
      return false;
    }
  }

  frame->serial_id = ((static_cast<uint32_t>(words[0] >> 1U) & 0xFFU) << 16U) |
                     ((static_cast<uint32_t>(words[1] >> 1U) & 0xFFU) << 8U) |
                     (static_cast<uint32_t>(words[2] >> 1U) & 0xFFU);
  frame->cmd1 = static_cast<uint8_t>((words[3] >> 1U) & 0xFFU);
  frame->cmd2 = static_cast<uint8_t>((words[4] >> 1U) & 0xFFU);
  frame->err1 = static_cast<uint8_t>((words[5] >> 1U) & 0xFFU);
  frame->err2 = static_cast<uint8_t>((words[6] >> 1U) & 0xFFU);
  frame->bit_offset = bit_offset;
  frame->symbol_offset = symbol_offset;
  frame->absolute_bit_offset = static_cast<uint32_t>(bit_offset) + (static_cast<uint32_t>(symbol_offset) * 2U);
  *reject_reason = "candidate_structural_match";
  return true;
}

bool same_frame(const CandidateFrame& left, const CandidateFrame& right) {
  return left.serial_id == right.serial_id && left.cmd1 == right.cmd1 && left.err1 == right.err1 &&
         left.cmd2 == right.cmd2 && left.err2 == right.err2;
}

bool observed_state_valid(uint8_t cmd1, uint8_t cmd2) {
  const bool power = (cmd1 & 0x01U) != 0U;
  const bool thermostat = (cmd1 & 0x02U) != 0U;
  const uint8_t light = static_cast<uint8_t>((cmd1 >> 4U) & 0x07U);
  const uint8_t flame = static_cast<uint8_t>(cmd2 & 0x07U);
  const uint8_t fan = static_cast<uint8_t>((cmd2 >> 4U) & 0x07U);
  if (light > 7U || fan > 6U) {
    return false;
  }
  if (!power) {
    return flame <= 6U;
  }
  if (thermostat) {
    return flame <= 6U;
  }
  return flame >= 1U && flame <= 6U;
}

void copy_frame_to_decoded(const CandidateFrame& frame, Proflame2DecodedPacket* decoded) {
  decoded->candidate_seen = true;
  decoded->serial_id = frame.serial_id;
  decoded->cmd1 = frame.cmd1;
  decoded->err1 = frame.err1;
  decoded->cmd2 = frame.cmd2;
  decoded->err2 = frame.err2;
  decoded->power = (frame.cmd1 & 0x01U) != 0U ? 1U : 0U;
  decoded->thermostat = (frame.cmd1 & 0x02U) != 0U ? 1U : 0U;
  decoded->light = static_cast<uint8_t>((frame.cmd1 >> 4U) & 0x07U);
  decoded->cpi = (frame.cmd1 & 0x80U) != 0U ? 1U : 0U;
  decoded->flame = static_cast<uint8_t>(frame.cmd2 & 0x07U);
  decoded->aux = (frame.cmd2 & 0x08U) != 0U ? 1U : 0U;
  decoded->fan = static_cast<uint8_t>((frame.cmd2 >> 4U) & 0x07U);
  decoded->front = (frame.cmd2 & 0x80U) != 0U ? 1U : 0U;
  decoded->repeat_count = frame.repeat_count;
  decoded->bit_offset = frame.bit_offset;
  decoded->symbol_offset = frame.symbol_offset;
  decoded->absolute_bit_offset = frame.absolute_bit_offset;
  decoded->raw_slice_start_byte = static_cast<uint16_t>(frame.absolute_bit_offset / 8U);
  const uint32_t raw_slice_end = (frame.absolute_bit_offset + TOTAL_AIR_BITS + 7U) / 8U;
  decoded->raw_slice_length = raw_slice_end > decoded->raw_slice_start_byte
                                  ? static_cast<uint16_t>(raw_slice_end - decoded->raw_slice_start_byte)
                                  : 0U;
}

} // namespace

uint8_t proflame2_build_err_byte(uint8_t command, uint8_t c_value, uint8_t d_value) {
  command &= 0xFFU;
  c_value &= 0x0FU;
  d_value &= 0x0FU;
  const uint8_t high_nibble = static_cast<uint8_t>((command >> 4U) & 0x0FU);
  const uint8_t low_nibble = static_cast<uint8_t>(command & 0x0FU);
  const uint8_t err_high = static_cast<uint8_t>(
      (c_value ^ high_nibble ^ ((high_nibble << 1U) & 0x0FU) ^ ((low_nibble << 1U) & 0x0FU)) & 0x0FU);
  const uint8_t err_low = static_cast<uint8_t>((d_value ^ high_nibble ^ low_nibble) & 0x0FU);
  return static_cast<uint8_t>((err_high << 4U) | err_low);
}

bool proflame2_decode_fifo_window(const uint8_t* payload, size_t length, const Proflame2DecodeProfile& profile,
                                  Proflame2DecodedPacket* decoded) {
  if (decoded == nullptr) {
    return false;
  }
  *decoded = Proflame2DecodedPacket{};
  if (payload == nullptr || length == 0U) {
    decoded->reject_reason = "payload_empty";
    return false;
  }
  if (!profile.enabled || profile.serial_id == 0U) {
    decoded->reject_reason = "profile_not_configured";
    return false;
  }
  const uint32_t bit_length = static_cast<uint32_t>(length * 8U);
  if (bit_length < TOTAL_AIR_BITS) {
    decoded->reject_reason = "payload_too_short";
    return false;
  }

  std::array<CandidateFrame, MAX_OWNER_CANDIDATES> candidates{};
  const char* last_reject_reason = "no_valid_candidate";
  bool saw_structural_candidate = false;
  bool saw_wrong_serial = false;
  bool saw_ecc_failure = false;
  bool saw_invalid_state = false;
  CandidateFrame diagnostic_candidate;
  bool diagnostic_candidate_seen = false;
  uint8_t structural_candidate_count = 0U;

  for (uint8_t bit_offset = 0U; bit_offset < 8U && bit_offset < bit_length; bit_offset++) {
    const uint32_t symbol_count = (bit_length - bit_offset) / 2U;
    if (symbol_count < TOTAL_SYMBOLS) {
      continue;
    }
    for (uint16_t symbol_offset = 0U; symbol_offset <= symbol_count - TOTAL_SYMBOLS; symbol_offset++) {
      CandidateFrame candidate;
      const char* reject_reason = nullptr;
      if (!parse_symbol_window(payload, length, bit_offset, symbol_offset, &candidate, &reject_reason)) {
        last_reject_reason = reject_reason;
        continue;
      }
      saw_structural_candidate = true;
      if (structural_candidate_count < UINT8_MAX) {
        structural_candidate_count++;
      }
      if (!diagnostic_candidate_seen || candidate.serial_id == profile.serial_id) {
        diagnostic_candidate = candidate;
        diagnostic_candidate_seen = true;
      }
      if (candidate.serial_id != profile.serial_id) {
        saw_wrong_serial = true;
        continue;
      }
      if (diagnostic_candidate.serial_id != profile.serial_id) {
        diagnostic_candidate = candidate;
      }
      if (candidate.err1 != proflame2_build_err_byte(candidate.cmd1, profile.c1, profile.d1) ||
          candidate.err2 != proflame2_build_err_byte(candidate.cmd2, profile.c2, profile.d2)) {
        saw_ecc_failure = true;
        continue;
      }
      if (!observed_state_valid(candidate.cmd1, candidate.cmd2)) {
        saw_invalid_state = true;
        continue;
      }

      bool recorded = false;
      for (auto& existing : candidates) {
        if (existing.used && same_frame(existing, candidate)) {
          if (existing.repeat_count < UINT8_MAX) {
            existing.repeat_count++;
          }
          recorded = true;
          break;
        }
      }
      if (recorded) {
        continue;
      }
      for (auto& slot : candidates) {
        if (!slot.used) {
          slot = candidate;
          slot.used = true;
          slot.repeat_count = 1U;
          recorded = true;
          break;
        }
      }
    }
  }

  const CandidateFrame* best = nullptr;
  for (const auto& candidate : candidates) {
    if (!candidate.used) {
      continue;
    }
    if (best == nullptr || candidate.repeat_count > best->repeat_count) {
      best = &candidate;
    }
  }

  if (best == nullptr) {
    decoded->candidate_count = structural_candidate_count;
    if (diagnostic_candidate_seen) {
      copy_frame_to_decoded(diagnostic_candidate, decoded);
      decoded->serial_matched = diagnostic_candidate.serial_id == profile.serial_id;
      decoded->ecc_matched =
          decoded->serial_matched &&
          diagnostic_candidate.err1 == proflame2_build_err_byte(diagnostic_candidate.cmd1, profile.c1, profile.d1) &&
          diagnostic_candidate.err2 == proflame2_build_err_byte(diagnostic_candidate.cmd2, profile.c2, profile.d2);
    }
    decoded->reject_reason = saw_wrong_serial           ? "wrong_serial_id"
                             : saw_ecc_failure          ? "ecc_mismatch"
                             : saw_invalid_state        ? "invalid_observed_state"
                             : saw_structural_candidate ? "no_matching_profile_candidate"
                                                        : last_reject_reason;
    return false;
  }

  copy_frame_to_decoded(*best, decoded);
  decoded->candidate_count = structural_candidate_count;
  decoded->serial_matched = true;
  decoded->ecc_matched = true;
  const uint8_t confidence_repeat = best->repeat_count > 7U ? 7U : best->repeat_count;
  decoded->confidence = static_cast<uint8_t>(100U + (confidence_repeat * 20U));
  decoded->reject_reason = "accepted";
  return true;
}

} // namespace proflame2_tembed
} // namespace esphome
