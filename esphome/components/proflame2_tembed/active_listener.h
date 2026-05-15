// Active-listener semantic policy for FIFO RX windows.
//
// The component owns radio IO, FIFO buffering, telemetry counters, and HA event
// publication. This helper owns the active-listener decision boundary: validate
// whether a FIFO window contains a decoded Proflame2 packet for the learned
// profile, classify rejection reasons, and suppress duplicate repeats.

#pragma once

#include <cstdint>

#include "proflame2_decoder.h"

namespace esphome {
namespace proflame2_tembed {

enum class ActiveListenerOutcomeType : uint8_t {
  IDLE_NO_CANDIDATE = 0,
  DROPPED = 1,
  DUPLICATE = 2,
  ACCEPTED = 3,
};

struct ActiveListenerOutcome {
  ActiveListenerOutcomeType type{ActiveListenerOutcomeType::DROPPED};
  const char* stage{"decode_failed"};
  const char* reason{"unknown"};
  Proflame2DecodedPacket decoded{};
  uint32_t duplicate_age_ms{0};
};

class ActiveListenerController {
public:
  ActiveListenerOutcome evaluate_window(const uint8_t* selected_bytes, uint16_t selected_count,
                                        bool trailing_window_complete, const Proflame2DecodeProfile& profile,
                                        uint32_t complete_ms, uint32_t dedup_ms);

private:
  bool is_duplicate_(const Proflame2DecodedPacket& decoded, uint32_t complete_ms, uint32_t dedup_ms,
                     uint32_t* age_ms) const;
  void remember_accepted_(const Proflame2DecodedPacket& decoded, uint32_t complete_ms);

  uint32_t last_accepted_packet_tick_ms_{0};
  uint32_t last_accepted_serial_id_{0};
  uint8_t last_accepted_cmd1_{0};
  uint8_t last_accepted_cmd2_{0};
  uint8_t last_accepted_err1_{0};
  uint8_t last_accepted_err2_{0};
};

} // namespace proflame2_tembed
} // namespace esphome
