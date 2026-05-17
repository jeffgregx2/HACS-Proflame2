#pragma once

#include <cstddef>
#include <cstdint>

namespace esphome {
namespace proflame2_tembed {

/// Strict acceptance profile learned from a canonical Proflame2 remote.
///
/// When enabled is false, the decoder may identify Proflame2-shaped packets but
/// must not treat them as packets owned by this controller. When enabled is
/// true, accepted packets must match the learned serial ID and command ECC
/// profile before they are published as semantic RX events.
struct Proflame2DecodeProfile {
  bool enabled{false};
  uint32_t serial_id{0};
  uint8_t c1{0};
  uint8_t d1{0};
  uint8_t c2{0};
  uint8_t d2{0};
};

/// Firmware-visible decode result for one raw CC1101 FIFO byte window.
///
/// On success this describes the selected accepted packet. On failure it keeps
/// the best rejection context so diagnostics can distinguish no candidate,
/// serial mismatch, ECC/profile mismatch, and malformed packet windows.
struct Proflame2DecodedPacket {
  uint32_t serial_id{0};
  uint8_t cmd1{0};
  uint8_t err1{0};
  uint8_t cmd2{0};
  uint8_t err2{0};
  uint8_t power{0};
  uint8_t flame{0};
  uint8_t fan{0};
  uint8_t light{0};
  uint8_t front{0};
  uint8_t aux{0};
  uint8_t thermostat{0};
  uint8_t cpi{0};
  uint8_t repeat_count{0};
  uint8_t confidence{0};
  uint8_t bit_offset{0};
  uint16_t symbol_offset{0};
  uint32_t absolute_bit_offset{0};
  /// Byte range inside the caller-owned FIFO payload that produced the selected
  /// candidate or the best rejection context.
  uint16_t raw_slice_start_byte{0};
  uint16_t raw_slice_length{0};
  /// A syntactically plausible Proflame2 candidate was found before profile
  /// acceptance checks were applied.
  bool candidate_seen{false};
  /// The decoded candidate serial matched the enabled profile serial.
  bool serial_matched{false};
  /// Command checksum/ECC bytes matched the enabled profile C/D values.
  bool ecc_matched{false};
  /// Number of candidate packet windows considered in this FIFO payload.
  uint8_t candidate_count{0};
  /// Static diagnostic reason owned by the decoder; callers must not free it.
  const char* reject_reason{"not_decoded"};
};

/// Build the command error/check byte used to validate a decoded command
/// against the learned C/D profile in firmware.
uint8_t proflame2_build_err_byte(uint8_t command, uint8_t c_value, uint8_t d_value);

/// Scan a raw CC1101 FIFO byte window for accepted Proflame2 packet candidates.
///
/// The input payload is a raw slicer window and is not packet-owned by itself.
/// This function scans bit/symbol offsets, applies profile policy, and returns
/// true only when an accepted semantic packet is found. On false, decoded is
/// still populated with rejection context when provided. The caller retains
/// ownership of payload; no heap ownership is transferred.
bool proflame2_decode_fifo_window(const uint8_t* payload, size_t length, const Proflame2DecodeProfile& profile,
                                  Proflame2DecodedPacket* decoded);

} // namespace proflame2_tembed
} // namespace esphome
