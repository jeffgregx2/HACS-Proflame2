// Conservative fallback selection for malformed, one-extra-bit RMT repeats.
// HA still owns state acceptance; this helper never repairs captured data.
#pragma once

#include <array>
#include <cstdint>

#include "proflame2_decoder.h"
#include "rmt_ook_capture.h"

namespace esphome {
namespace proflame2_tembed {

using RmtFrameWords = std::array<uint8_t, 10>;

// Exact-bit framing only, shared by both handlers and the preservation probe.
// Only a single missing final guard LOW at the exact row boundary is allowed.
bool rmt_read_frame_words(const RmtOokCapture& capture, uint16_t offset, uint8_t word_count, RmtFrameWords& words);
bool rmt_capture_has_frame(const RmtOokCapture& capture);
uint32_t rmt_add_skipped_count(uint32_t total, uint32_t increment);

struct RmtSelectionResult {
  bool replaced{false};
  uint32_t skipped_malformed{0};
  uint16_t agreeing_repeats{0};
};

struct RmtFrameHandler;

class RmtRepeatSelection {
public:
  RmtRepeatSelection(bool active_listening, bool learning, const Proflame2DecodeProfile& profile);
  bool enabled() const {
    return this->handler_ != nullptr;
  }
  const char* format_name() const;
  static void observe_segment(const RmtOokCapture& capture, void* context);
  void observe(const RmtOokCapture& capture);
  // Consumed once after successful conversion/rearm. Further calls return zero
  // deltas, preventing repeated telemetry publication from recounting a window.
  RmtSelectionResult finish(RmtOokCapture& original);

private:
  enum class Validation : uint8_t { UNEXAMINED, MALFORMED, FOREIGN, VALID };
  Validation validate_words_(const RmtFrameWords& words) const;
  Validation inspect_(const RmtOokCapture& capture) const;

  const RmtFrameHandler* handler_{nullptr};
  Proflame2DecodeProfile profile_{};
  RmtOokCapture alternative_{};
  RmtFrameWords alternative_words_{};
  uint32_t malformed_{0};
  uint16_t agreeing_{0};
  bool conflicting_{false};
  bool finished_{false};
};

} // namespace proflame2_tembed
} // namespace esphome
