#include "active_listener.h"

#include <cstring>

namespace esphome {
namespace proflame2_tembed {

namespace {

bool profile_mismatch_rejection_(const Proflame2DecodedPacket& decoded) {
  if (!decoded.serial_matched && !decoded.candidate_seen) {
    return false;
  }
  return decoded.reject_reason == nullptr || strcmp(decoded.reject_reason, "wrong_serial_id") == 0 ||
         strcmp(decoded.reject_reason, "ecc_mismatch") == 0 ||
         strcmp(decoded.reject_reason, "no_matching_profile_candidate") == 0;
}

} // namespace

ActiveListenerOutcome ActiveListenerController::evaluate_window(const uint8_t* selected_bytes, uint16_t selected_count,
                                                                bool trailing_window_complete,
                                                                const Proflame2DecodeProfile& profile,
                                                                uint32_t complete_ms, uint32_t dedup_ms) {
  ActiveListenerOutcome outcome;
  if (selected_count == 0U) {
    outcome.type = ActiveListenerOutcomeType::DROPPED;
    outcome.stage = "no_rf_captured";
    outcome.reason = "selected_window_empty";
    return outcome;
  }
  if (!trailing_window_complete) {
    outcome.type = ActiveListenerOutcomeType::DROPPED;
    outcome.stage = "fifo_incomplete";
    outcome.reason = "incomplete_trailing_window";
    return outcome;
  }

  Proflame2DecodedPacket decoded;
  if (!proflame2_decode_fifo_window(selected_bytes, selected_count, profile, &decoded)) {
    outcome.decoded = decoded;
    outcome.reason = decoded.reject_reason;
    if (!decoded.candidate_seen) {
      outcome.type = ActiveListenerOutcomeType::IDLE_NO_CANDIDATE;
      outcome.stage = "idle_noise";
      return outcome;
    }
    outcome.type = ActiveListenerOutcomeType::DROPPED;
    outcome.stage = profile_mismatch_rejection_(decoded) ? "profile_mismatch" : "decode_failed";
    return outcome;
  }

  uint32_t duplicate_age_ms = 0U;
  if (this->is_duplicate_(decoded, complete_ms, dedup_ms, &duplicate_age_ms)) {
    outcome.type = ActiveListenerOutcomeType::DUPLICATE;
    outcome.reason = "duplicate_recent_packet";
    outcome.decoded = decoded;
    outcome.duplicate_age_ms = duplicate_age_ms;
    return outcome;
  }

  this->remember_accepted_(decoded, complete_ms);
  outcome.type = ActiveListenerOutcomeType::ACCEPTED;
  outcome.stage = "accepted";
  outcome.reason = "accepted";
  outcome.decoded = decoded;
  return outcome;
}

bool ActiveListenerController::is_duplicate_(const Proflame2DecodedPacket& decoded, uint32_t complete_ms,
                                             uint32_t dedup_ms, uint32_t* age_ms) const {
  if (this->last_accepted_packet_tick_ms_ == 0U) {
    return false;
  }
  const uint32_t age = complete_ms - this->last_accepted_packet_tick_ms_;
  if (age_ms != nullptr) {
    *age_ms = age;
  }
  return age < dedup_ms && this->last_accepted_serial_id_ == (decoded.serial_id & 0xFFFFFFU) &&
         this->last_accepted_cmd1_ == decoded.cmd1 && this->last_accepted_cmd2_ == decoded.cmd2 &&
         this->last_accepted_err1_ == decoded.err1 && this->last_accepted_err2_ == decoded.err2;
}

void ActiveListenerController::remember_accepted_(const Proflame2DecodedPacket& decoded, uint32_t complete_ms) {
  this->last_accepted_packet_tick_ms_ = complete_ms;
  this->last_accepted_serial_id_ = decoded.serial_id & 0xFFFFFFU;
  this->last_accepted_cmd1_ = decoded.cmd1;
  this->last_accepted_cmd2_ = decoded.cmd2;
  this->last_accepted_err1_ = decoded.err1;
  this->last_accepted_err2_ = decoded.err2;
}

} // namespace proflame2_tembed
} // namespace esphome
