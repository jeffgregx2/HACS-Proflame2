#include "tx_controller.h"

namespace esphome {
namespace proflame2_tembed {

namespace {

bool is_hex_digit_(char character) {
  return (character >= '0' && character <= '9') || (character >= 'a' && character <= 'f') ||
         (character >= 'A' && character <= 'F');
}

uint8_t hex_nibble_(char character) {
  if (character >= '0' && character <= '9') {
    return static_cast<uint8_t>(character - '0');
  }
  if (character >= 'a' && character <= 'f') {
    return static_cast<uint8_t>(character - 'a' + 10);
  }
  return static_cast<uint8_t>(character - 'A' + 10);
}

} // namespace

TxValidationResult TxController::validate_payload_request(const std::string& air_payload_hex,
                                                          uint32_t payload_bit_length, uint8_t repeat_count,
                                                          const TxValidationConfig& config) {
  TxValidationResult result;
  result.prepared.effective_repeat_count =
      effective_repeat_count(repeat_count, config.diagnostic_repeat_count_override);

  if (!is_hex_payload_(air_payload_hex)) {
    result.reject_reason = TxValidationRejectReason::INVALID_HEX_PAYLOAD;
    return result;
  }
  if (config.diagnostic_repeat_count_override == 0U && repeat_count != config.configured_repeat_count) {
    result.reject_reason = TxValidationRejectReason::REPEAT_COUNT_MISMATCH;
    return result;
  }
  if (!decode_hex_payload_(air_payload_hex, &result.prepared.payload)) {
    result.reject_reason = TxValidationRejectReason::INVALID_HEX_PAYLOAD;
    return result;
  }

  result.prepared.max_payload_bits = static_cast<uint32_t>(result.prepared.payload.size() * 8U);
  if (payload_bit_length == 0U || payload_bit_length > result.prepared.max_payload_bits) {
    result.reject_reason = TxValidationRejectReason::INVALID_PAYLOAD_BIT_LENGTH;
    return result;
  }
  if (config.payload_bit_length_override > result.prepared.max_payload_bits) {
    result.reject_reason = TxValidationRejectReason::INVALID_PAYLOAD_BIT_LENGTH_OVERRIDE;
    return result;
  }

  result.prepared.effective_payload_bit_length =
      config.payload_bit_length_override > 0U ? config.payload_bit_length_override : payload_bit_length;
  return result;
}

uint8_t TxController::effective_repeat_count(uint8_t repeat_count, uint8_t diagnostic_repeat_count_override) {
  return diagnostic_repeat_count_override > 0U ? diagnostic_repeat_count_override : repeat_count;
}

const char* TxController::reject_reason_to_error_code(TxValidationRejectReason reason) {
  switch (reason) {
  case TxValidationRejectReason::NONE:
    return "";
  case TxValidationRejectReason::INVALID_HEX_PAYLOAD:
    return "invalid_hex_payload";
  case TxValidationRejectReason::REPEAT_COUNT_MISMATCH:
    return "repeat_count_mismatch";
  case TxValidationRejectReason::INVALID_PAYLOAD_BIT_LENGTH:
    return "invalid_payload_bit_length";
  case TxValidationRejectReason::INVALID_PAYLOAD_BIT_LENGTH_OVERRIDE:
    return "invalid_payload_bit_length_override";
  default:
    return "unknown_tx_validation_error";
  }
}

bool TxController::is_hex_payload_(const std::string& value) {
  if (value.empty() || (value.size() % 2U) != 0U) {
    return false;
  }
  for (char character : value) {
    if (!is_hex_digit_(character)) {
      return false;
    }
  }
  return true;
}

bool TxController::decode_hex_payload_(const std::string& value, std::vector<uint8_t>* payload) {
  if (!is_hex_payload_(value) || payload == nullptr) {
    return false;
  }

  payload->clear();
  payload->reserve(value.size() / 2U);
  for (size_t i = 0; i < value.size(); i += 2U) {
    const uint8_t high = hex_nibble_(value[i]);
    const uint8_t low = hex_nibble_(value[i + 1U]);
    payload->push_back(static_cast<uint8_t>((high << 4U) | low));
  }
  return true;
}

} // namespace proflame2_tembed
} // namespace esphome
