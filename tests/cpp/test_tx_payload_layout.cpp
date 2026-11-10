#include <cassert>
#include <limits>
#include <string>

#include "tx_controller.h"
#include "tx_payload_layout.h"
#include "rf_band.h"

using esphome::proflame2_tembed::NativePayloadLayout;
using esphome::proflame2_tembed::PROFLAME_EXTENDED_WORD_COUNT;
using esphome::proflame2_tembed::PROFLAME_LEGACY_WORD_COUNT;
using esphome::proflame2_tembed::PROFLAME_MAX_TRAILER_SYMBOLS;
using esphome::proflame2_tembed::PROFLAME_SYMBOLS_PER_WORD;
using esphome::proflame2_tembed::RFBand;
using esphome::proflame2_tembed::RFBandConfiguration;
using esphome::proflame2_tembed::TxController;
using esphome::proflame2_tembed::TxValidationConfig;
using esphome::proflame2_tembed::TxValidationRejectReason;
using esphome::proflame2_tembed::derive_native_payload_layout;
using esphome::proflame2_tembed::resolve_rf_band_configuration;
using esphome::proflame2_tembed::resolve_tx_frequency;

int main() {
  RFBandConfiguration rf_band{};
  assert(resolve_rf_band_configuration(RFBand::BAND_315, rf_band));
  assert(rf_band.frequency_hz == 314973000U);
  assert(rf_band.rf_switch_sw1_high);
  assert(!rf_band.rf_switch_sw0_high);
  assert(std::string(rf_band.name) == "315 MHz");

  assert(resolve_rf_band_configuration(RFBand::BAND_433, rf_band));
  assert(rf_band.frequency_hz == 433920000U);
  assert(rf_band.rf_switch_sw1_high);
  assert(rf_band.rf_switch_sw0_high);
  assert(std::string(rf_band.name) == "433.92 MHz");

  assert(!resolve_rf_band_configuration(static_cast<RFBand>(99U), rf_band));

  for (const auto band : {RFBand::BAND_315, RFBand::BAND_433}) {
    assert(resolve_rf_band_configuration(band, rf_band));
    const uint32_t original_rx_hz = rf_band.frequency_hz;
    for (const int32_t offset : {0, 60000, -60000, 100000, -100000}) {
      uint32_t tx_hz = 0;
      assert(resolve_tx_frequency(band, offset, tx_hz));
      assert(static_cast<int64_t>(tx_hz) == static_cast<int64_t>(original_rx_hz) + offset);
      assert(rf_band.frequency_hz == original_rx_hz);
    }
    for (const int32_t offset : {100001, -100001, std::numeric_limits<int32_t>::max(),
                                  std::numeric_limits<int32_t>::min()}) {
      uint32_t tx_hz = 123;
      assert(!resolve_tx_frequency(band, offset, tx_hz));
      assert(tx_hz == 123);
    }
  }
  uint32_t invalid_band_hz = 123;
  assert(!resolve_tx_frequency(static_cast<RFBand>(99U), 0, invalid_band_hz));
  assert(invalid_band_hz == 123);

  NativePayloadLayout layout{};
  assert(derive_native_payload_layout(PROFLAME_LEGACY_WORD_COUNT * PROFLAME_SYMBOLS_PER_WORD, layout));
  assert(layout.word_count == PROFLAME_LEGACY_WORD_COUNT);
  assert(layout.trailing_symbol_count == 0U);

  assert(derive_native_payload_layout(PROFLAME_LEGACY_WORD_COUNT * PROFLAME_SYMBOLS_PER_WORD +
                                          PROFLAME_MAX_TRAILER_SYMBOLS,
                                      layout));
  assert(layout.word_count == PROFLAME_LEGACY_WORD_COUNT);
  assert(layout.trailing_symbol_count == PROFLAME_MAX_TRAILER_SYMBOLS);

  assert(derive_native_payload_layout(PROFLAME_EXTENDED_WORD_COUNT * PROFLAME_SYMBOLS_PER_WORD, layout));
  assert(layout.word_count == PROFLAME_EXTENDED_WORD_COUNT);
  assert(layout.trailing_symbol_count == 0U);

  assert(derive_native_payload_layout(PROFLAME_EXTENDED_WORD_COUNT * PROFLAME_SYMBOLS_PER_WORD +
                                          PROFLAME_MAX_TRAILER_SYMBOLS,
                                      layout));
  assert(layout.word_count == PROFLAME_EXTENDED_WORD_COUNT);
  assert(layout.trailing_symbol_count == PROFLAME_MAX_TRAILER_SYMBOLS);

  assert(!derive_native_payload_layout(PROFLAME_LEGACY_WORD_COUNT * PROFLAME_SYMBOLS_PER_WORD - 1U, layout));
  assert(!derive_native_payload_layout(PROFLAME_LEGACY_WORD_COUNT * PROFLAME_SYMBOLS_PER_WORD +
                                           PROFLAME_MAX_TRAILER_SYMBOLS + 1U,
                                       layout));
  assert(!derive_native_payload_layout(PROFLAME_EXTENDED_WORD_COUNT * PROFLAME_SYMBOLS_PER_WORD +
                                           PROFLAME_MAX_TRAILER_SYMBOLS + 1U,
                                       layout));

  const TxValidationConfig default_config{
      .configured_repeat_count = 5U,
      .diagnostic_repeat_count_override = 0U,
      .payload_bit_length_override = 0U,
  };
  const auto legacy = TxController::validate_payload_request(std::string(50U, 'a'), 182U, 5U, default_config);
  assert(legacy.accepted());
  assert(legacy.prepared.payload.size() == 25U);
  assert(legacy.prepared.effective_payload_bit_length == 182U);
  assert(legacy.prepared.effective_repeat_count == 5U);

  const auto extended = TxController::validate_payload_request(std::string(70U, 'b'), 260U, 5U, default_config);
  assert(extended.accepted());
  assert(extended.prepared.payload.size() == 35U);
  assert(extended.prepared.effective_payload_bit_length == 260U);
  assert(extended.prepared.effective_repeat_count == 5U);

  const TxValidationConfig diagnostic_config{
      .configured_repeat_count = 5U,
      .diagnostic_repeat_count_override = 3U,
      .payload_bit_length_override = 0U,
  };
  const auto diagnostic = TxController::validate_payload_request(std::string(70U, 'B'), 260U, 1U, diagnostic_config);
  assert(diagnostic.accepted());
  assert(diagnostic.prepared.effective_repeat_count == 3U);

  const TxValidationConfig explicit_legacy_override{
      .configured_repeat_count = 5U,
      .diagnostic_repeat_count_override = 0U,
      .payload_bit_length_override = 182U,
  };
  const auto override =
      TxController::validate_payload_request(std::string(70U, 'c'), 260U, 5U, explicit_legacy_override);
  assert(override.accepted());
  assert(override.prepared.effective_payload_bit_length == 182U);

  const auto invalid_hex = TxController::validate_payload_request("0g", 8U, 5U, default_config);
  assert(invalid_hex.reject_reason == TxValidationRejectReason::INVALID_HEX_PAYLOAD);
  const auto invalid_hex_with_repeat_mismatch =
      TxController::validate_payload_request("0g", 8U, 4U, default_config);
  assert(invalid_hex_with_repeat_mismatch.reject_reason == TxValidationRejectReason::INVALID_HEX_PAYLOAD);
  const auto empty_hex = TxController::validate_payload_request("", 8U, 5U, default_config);
  assert(empty_hex.reject_reason == TxValidationRejectReason::INVALID_HEX_PAYLOAD);
  const auto odd_hex = TxController::validate_payload_request("a", 4U, 5U, default_config);
  assert(odd_hex.reject_reason == TxValidationRejectReason::INVALID_HEX_PAYLOAD);
  const auto decimal_hex = TxController::validate_payload_request("10", 8U, 5U, default_config);
  assert(decimal_hex.accepted());
  const auto repeat_mismatch = TxController::validate_payload_request("aa", 8U, 4U, default_config);
  assert(repeat_mismatch.reject_reason == TxValidationRejectReason::REPEAT_COUNT_MISMATCH);
  const auto invalid_length = TxController::validate_payload_request("aa", 9U, 5U, default_config);
  assert(invalid_length.reject_reason == TxValidationRejectReason::INVALID_PAYLOAD_BIT_LENGTH);
  const TxValidationConfig invalid_override{
      .configured_repeat_count = 5U,
      .diagnostic_repeat_count_override = 0U,
      .payload_bit_length_override = 9U,
  };
  const auto override_too_long = TxController::validate_payload_request("aa", 8U, 5U, invalid_override);
  assert(override_too_long.reject_reason == TxValidationRejectReason::INVALID_PAYLOAD_BIT_LENGTH_OVERRIDE);

  assert(std::string(TxController::reject_reason_to_error_code(TxValidationRejectReason::NONE)).empty());
  assert(std::string(TxController::reject_reason_to_error_code(TxValidationRejectReason::INVALID_HEX_PAYLOAD)) ==
         "invalid_hex_payload");
  assert(std::string(TxController::reject_reason_to_error_code(TxValidationRejectReason::REPEAT_COUNT_MISMATCH)) ==
         "repeat_count_mismatch");
  assert(std::string(TxController::reject_reason_to_error_code(TxValidationRejectReason::INVALID_PAYLOAD_BIT_LENGTH)) ==
         "invalid_payload_bit_length");
  assert(std::string(TxController::reject_reason_to_error_code(TxValidationRejectReason::INVALID_PAYLOAD_BIT_LENGTH_OVERRIDE)) ==
         "invalid_payload_bit_length_override");
  assert(std::string(TxController::reject_reason_to_error_code(static_cast<TxValidationRejectReason>(99U))) ==
         "unknown_tx_validation_error");

  return 0;
}
