// Native Proflame2 transmit payload layout helpers.
//
// Home Assistant supplies a Manchester-coded payload with either seven legacy
// words or ten extended words. The radio timing path uses this helper to
// determine how many complete words it must emit.

#pragma once

#include <cstddef>

namespace esphome {
namespace proflame2_tembed {

inline constexpr size_t PROFLAME_LEGACY_WORD_COUNT = 7;
inline constexpr size_t PROFLAME_EXTENDED_WORD_COUNT = 10;
inline constexpr size_t PROFLAME_SYMBOLS_PER_WORD = 13;
inline constexpr size_t PROFLAME_MAX_TRAILER_SYMBOLS = 9;

struct NativePayloadLayout {
  size_t word_count{0};
  size_t trailing_symbol_count{0};
};

/// Accept one complete legacy or extended payload, with an optional bounded
/// zero-symbol trailer. Partial extended frames are deliberately rejected.
bool derive_native_payload_layout(size_t symbol_count, NativePayloadLayout& layout);

} // namespace proflame2_tembed
} // namespace esphome
