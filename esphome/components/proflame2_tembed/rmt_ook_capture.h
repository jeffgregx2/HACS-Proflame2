// Transport-only PCM segment and metadata shared by RMT acquisition and selection.
#pragma once

#include <array>
#include <cstddef>
#include <cstdint>

namespace esphome {
namespace proflame2_tembed {

struct RmtOokCapture {
  static constexpr size_t MAX_PCM_BITS = 1024;

  std::array<uint8_t, MAX_PCM_BITS / 8> pcm_bytes{};
  uint16_t pcm_bit_count{0};
  uint16_t symbol_count{0};
  uint16_t transition_count{0};
  uint32_t first_high_us{0};
  uint32_t first_low_us{0};
};

// Called synchronously before acquisition clears a completed segment. The
// observer must copy anything retained; it never owns the supplied capture.
using RmtSegmentObserver = void (*)(const RmtOokCapture& capture, void* context);

} // namespace proflame2_tembed
} // namespace esphome
