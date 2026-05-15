// TX request validation helpers for the Proflame2 T-Embed endpoint.
//
// Home Assistant owns semantic encoding and sends prepared air payload bytes as
// hex. This helper validates transport-level shape only: hex syntax, configured
// repeat-count policy, and requested bit length. Radio runtime queueing and RF
// timing remain owned by the ESPHome component and RadioCC1101.

#pragma once

#include <cstdint>
#include <string>
#include <vector>

namespace esphome {
namespace proflame2_tembed {

enum class TxValidationRejectReason : uint8_t {
  NONE = 0,
  INVALID_HEX_PAYLOAD = 1,
  REPEAT_COUNT_MISMATCH = 2,
  INVALID_PAYLOAD_BIT_LENGTH = 3,
  INVALID_PAYLOAD_BIT_LENGTH_OVERRIDE = 4,
};

struct TxValidationConfig {
  uint8_t configured_repeat_count{0};
  uint8_t diagnostic_repeat_count_override{0};
  uint32_t payload_bit_length_override{0};
};

struct TxPreparedPayload {
  std::vector<uint8_t> payload{};
  uint8_t effective_repeat_count{0};
  uint32_t max_payload_bits{0};
  uint32_t effective_payload_bit_length{0};
};

struct TxValidationResult {
  TxValidationRejectReason reject_reason{TxValidationRejectReason::NONE};
  TxPreparedPayload prepared{};

  bool accepted() const {
    return this->reject_reason == TxValidationRejectReason::NONE;
  }
};

class TxController {
public:
  static TxValidationResult validate_payload_request(const std::string& air_payload_hex, uint32_t payload_bit_length,
                                                     uint8_t repeat_count, const TxValidationConfig& config);
  static uint8_t effective_repeat_count(uint8_t repeat_count, uint8_t diagnostic_repeat_count_override);
  static const char* reject_reason_to_error_code(TxValidationRejectReason reason);

private:
  static bool is_hex_payload_(const std::string& value);
  static bool decode_hex_payload_(const std::string& value, std::vector<uint8_t>* payload);
};

} // namespace proflame2_tembed
} // namespace esphome
