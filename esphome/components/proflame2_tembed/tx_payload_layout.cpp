#include "tx_payload_layout.h"

namespace esphome {
namespace proflame2_tembed {

bool derive_native_payload_layout(size_t symbol_count, NativePayloadLayout& layout) {
  const size_t extended_symbols = PROFLAME_EXTENDED_WORD_COUNT * PROFLAME_SYMBOLS_PER_WORD;
  const size_t legacy_symbols = PROFLAME_LEGACY_WORD_COUNT * PROFLAME_SYMBOLS_PER_WORD;

  if (symbol_count >= extended_symbols && symbol_count <= extended_symbols + PROFLAME_MAX_TRAILER_SYMBOLS) {
    layout.word_count = PROFLAME_EXTENDED_WORD_COUNT;
    layout.trailing_symbol_count = symbol_count - extended_symbols;
    return true;
  }
  if (symbol_count >= legacy_symbols && symbol_count <= legacy_symbols + PROFLAME_MAX_TRAILER_SYMBOLS) {
    layout.word_count = PROFLAME_LEGACY_WORD_COUNT;
    layout.trailing_symbol_count = symbol_count - legacy_symbols;
    return true;
  }

  layout = NativePayloadLayout{};
  return false;
}

} // namespace proflame2_tembed
} // namespace esphome
