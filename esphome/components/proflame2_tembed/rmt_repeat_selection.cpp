#include "rmt_repeat_selection.h"

#include <algorithm>

namespace esphome {
namespace proflame2_tembed {

struct RmtFrameHandler {
  const char* name;
  uint8_t word_count;
  bool requires_profile;
  bool (*integrity_matches)(const RmtFrameWords&, const Proflame2DecodeProfile&);
  uint16_t normal_bits() const {
    return this->word_count * 26U - 1U;
  }
};

namespace {
bool bit_at(const RmtOokCapture& capture, uint16_t offset) {
  return (capture.pcm_bytes[offset / 8U] & (1U << (7U - offset % 8U))) != 0U;
}

bool zero_gap(const RmtOokCapture& capture, uint16_t offset, uint8_t count) {
  if (offset + count > capture.pcm_bit_count) {
    return false;
  }
  for (uint16_t i = offset; i < offset + count; i++) {
    if (bit_at(capture, i)) {
      return false;
    }
  }
  return true;
}

bool legacy_integrity(const RmtFrameWords& w, const Proflame2DecodeProfile& profile) {
  return w[5] == proflame2_build_err_byte(w[3], profile.c1, profile.d1) &&
         w[6] == proflame2_build_err_byte(w[4], profile.c2, profile.d2);
}

bool extended_integrity(const RmtFrameWords& w, const Proflame2DecodeProfile&) {
  uint8_t odd = 0;
  uint8_t even = 0;
  for (uint8_t i = 0; i < 8; i += 2) {
    odd = proflame2_build_err_byte(odd ^ w[i], 0, 0);
    even = proflame2_build_err_byte(even ^ w[i + 1], 0, 0);
  }
  return w[8] == odd && w[9] == even;
}

const RmtFrameHandler LEGACY{"legacy", 7, true, legacy_integrity};
const RmtFrameHandler EXTENDED{"extended", 10, false, extended_integrity};
} // namespace

bool rmt_read_frame_words(const RmtOokCapture& capture, uint16_t offset, uint8_t word_count, RmtFrameWords& words) {
  words.fill(0);
  if ((word_count != 7 && word_count != 10) || capture.pcm_bit_count > RmtOokCapture::MAX_PCM_BITS) {
    return false;
  }
  const uint32_t end = offset + word_count * 26U;
  const bool truncated = end == capture.pcm_bit_count + 1U;
  if (end > capture.pcm_bit_count && !truncated) {
    return false;
  }
  for (uint8_t word = 0; word < word_count; word++) {
    const uint16_t start = offset + word * 26U;
    // S=11 followed by 1=10. Check bounds before reading any guard.
    if (start + 24U >= capture.pcm_bit_count || !bit_at(capture, start) || !bit_at(capture, start + 1U) ||
        !bit_at(capture, start + 2U) || bit_at(capture, start + 3U) || !bit_at(capture, start + 24U)) {
      return false;
    }
    if (!(truncated && word + 1U == word_count) && bit_at(capture, start + 25U)) {
      return false;
    }
    uint16_t value = 0;
    uint8_t ones = 0;
    for (uint8_t symbol = 0; symbol < 10; symbol++) {
      const uint16_t at = start + 4U + symbol * 2U;
      const bool first = bit_at(capture, at);
      if (first == bit_at(capture, at + 1U)) {
        return false;
      }
      if (symbol < 9) {
        value = static_cast<uint16_t>((value << 1U) | first);
        ones += first;
      } else if (first != (ones % 2U != 0U)) {
        return false;
      }
    }
    if ((value & 1U) != (word == 0 ? 1U : 0U)) {
      return false;
    }
    words[word] = static_cast<uint8_t>(value >> 1U);
  }
  return true;
}

bool rmt_capture_has_frame(const RmtOokCapture& capture) {
  if (capture.pcm_bit_count > RmtOokCapture::MAX_PCM_BITS) {
    return false;
  }
  for (const uint8_t count : {7, 10}) {
    const uint16_t body = count * 26U;
    for (uint16_t offset = 0; offset + body <= capture.pcm_bit_count + 1U; offset++) {
      RmtFrameWords words{};
      if (!rmt_read_frame_words(capture, offset, count, words)) {
        continue;
      }
      const uint16_t end = offset + body;
      // More permissive than final acceptance: preserve even a complete body
      // without its trailer. A seven-word extended prefix is not a full row.
      if (end >= capture.pcm_bit_count || zero_gap(capture, end, count == 7 ? 18 : 10)) {
        return true;
      }
    }
  }
  return false;
}

uint32_t rmt_add_skipped_count(uint32_t total, uint32_t increment) {
  return UINT32_MAX - total < increment ? UINT32_MAX : total + increment;
}

RmtRepeatSelection::RmtRepeatSelection(bool active_listening, bool learning, const Proflame2DecodeProfile& profile)
    : profile_(profile) {
  if (!active_listening || learning) {
    return;
  }
  if (!profile.enabled) {
    // Extended profiles deliberately stay in raw mode; HA owns identity checks.
    this->handler_ = &EXTENDED;
  } else if (profile.serial_id > 0 && profile.serial_id <= 0xFFFFFFU && profile.c1 <= 15 && profile.d1 <= 15 &&
             profile.c2 <= 15 && profile.d2 <= 15) {
    this->handler_ = &LEGACY;
  }
}

const char* RmtRepeatSelection::format_name() const {
  return this->enabled() ? this->handler_->name : "disabled";
}

RmtRepeatSelection::Validation RmtRepeatSelection::validate_words_(const RmtFrameWords& words) const {
  const uint32_t serial = (static_cast<uint32_t>(words[0]) << 16U) | (static_cast<uint32_t>(words[1]) << 8U) | words[2];
  if (this->handler_->requires_profile && serial != this->profile_.serial_id) {
    return Validation::FOREIGN;
  }
  if (!this->handler_->integrity_matches(words, this->profile_) || (words[4] & 7U) > 6 || ((words[4] >> 4U) & 7U) > 6) {
    return Validation::MALFORMED;
  }
  return Validation::VALID;
}

RmtRepeatSelection::Validation RmtRepeatSelection::inspect_(const RmtOokCapture& capture) const {
  const uint16_t normal = this->handler_->normal_bits();
  if (capture.pcm_bit_count != normal && capture.pcm_bit_count != normal + 1U) {
    return Validation::UNEXAMINED;
  }
  bool foreign = false;
  for (uint16_t offset = 0; offset + normal <= capture.pcm_bit_count; offset++) {
    RmtFrameWords words{};
    if (!rmt_read_frame_words(capture, offset, this->handler_->word_count, words)) {
      continue;
    }
    const auto validation = this->validate_words_(words);
    if (validation == Validation::VALID) {
      return validation;
    }
    foreign |= validation == Validation::FOREIGN;
  }
  return foreign ? Validation::FOREIGN : Validation::MALFORMED;
}

void RmtRepeatSelection::observe_segment(const RmtOokCapture& capture, void* context) {
  if (context != nullptr) {
    static_cast<RmtRepeatSelection*>(context)->observe(capture);
  }
}

void RmtRepeatSelection::observe(const RmtOokCapture& capture) {
  if (!this->enabled() || this->finished_) {
    return;
  }
  if (this->inspect_(capture) == Validation::MALFORMED) {
    this->malformed_++;
  }
  if (capture.pcm_bit_count != this->handler_->normal_bits()) {
    return;
  }
  RmtFrameWords words{};
  if (!rmt_read_frame_words(capture, 0, this->handler_->word_count, words) ||
      this->validate_words_(words) != Validation::VALID) {
    return;
  }
  if (this->agreeing_ == 0) {
    this->alternative_ = capture;
    this->alternative_words_ = words;
  } else if (!std::equal(words.begin(), words.begin() + this->handler_->word_count, this->alternative_words_.begin())) {
    this->conflicting_ = true;
  }
  this->agreeing_++;
}

RmtSelectionResult RmtRepeatSelection::finish(RmtOokCapture& original) {
  if (!this->enabled() || this->finished_) {
    return {};
  }
  this->finished_ = true;
  RmtSelectionResult result{};
  result.skipped_malformed = this->malformed_;
  result.agreeing_repeats = this->agreeing_;
  if (original.pcm_bit_count == this->handler_->normal_bits() + 1U && this->agreeing_ != 0 && !this->conflicting_ &&
      !rmt_capture_has_frame(original)) {
    const uint16_t window_symbols = original.symbol_count;
    original = this->alternative_;
    original.symbol_count = window_symbols;
    result.replaced = true;
  } else if (result.skipped_malformed != 0 && this->inspect_(original) == Validation::MALFORMED) {
    // An unchanged raw winner is forwarded for HA's decision, not skipped here.
    result.skipped_malformed--;
  }
  return result;
}

} // namespace proflame2_tembed
} // namespace esphome
