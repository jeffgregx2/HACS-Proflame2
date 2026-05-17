// Direct CC1101 control for Proflame2 T-Embed transport and FIFO acquisition.
//
// This class intentionally does not implement Proflame2 encode/decode logic.
// It only configures the CC1101 and transmits the exact payload bytes supplied
// by Home Assistant, or captures raw CC1101 FIFO byte windows for higher-level
// scanner/decoder code.

#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <string>

#ifndef PROFLAME2_TEMBED_TX_DEBUG
#define PROFLAME2_TEMBED_TX_DEBUG 1
#endif

#ifndef PROFLAME2_TEMBED_RADIO_RUNTIME_STUB
#define PROFLAME2_TEMBED_RADIO_RUNTIME_STUB 0
#endif

#ifndef PROFLAME2_TX_CLEAN_MODE
#define PROFLAME2_TX_CLEAN_MODE 1
#endif

#include "esphome/components/spi/spi.h"
#include "esphome/core/gpio.h"

namespace esphome {
namespace proflame2_tembed {

inline constexpr uint8_t CC1101_IOCFG2 = 0x00;
inline constexpr uint8_t CC1101_IOCFG1 = 0x01;
inline constexpr uint8_t CC1101_IOCFG0 = 0x02;
inline constexpr uint8_t CC1101_FIFOTHR = 0x03;
inline constexpr uint8_t CC1101_SYNC1 = 0x04;
inline constexpr uint8_t CC1101_SYNC0 = 0x05;
inline constexpr uint8_t CC1101_PKTLEN = 0x06;
inline constexpr uint8_t CC1101_PKTCTRL1 = 0x07;
inline constexpr uint8_t CC1101_PKTCTRL0 = 0x08;
inline constexpr uint8_t CC1101_FSCTRL1 = 0x0B;
inline constexpr uint8_t CC1101_FSCTRL0 = 0x0C;
inline constexpr uint8_t CC1101_FREQ2 = 0x0D;
inline constexpr uint8_t CC1101_FREQ1 = 0x0E;
inline constexpr uint8_t CC1101_FREQ0 = 0x0F;
inline constexpr uint8_t CC1101_MDMCFG4 = 0x10;
inline constexpr uint8_t CC1101_MDMCFG3 = 0x11;
inline constexpr uint8_t CC1101_MDMCFG2 = 0x12;
inline constexpr uint8_t CC1101_MDMCFG1 = 0x13;
inline constexpr uint8_t CC1101_MDMCFG0 = 0x14;
inline constexpr uint8_t CC1101_DEVIATN = 0x15;
inline constexpr uint8_t CC1101_MCSM1 = 0x17;
inline constexpr uint8_t CC1101_MCSM0 = 0x18;
inline constexpr uint8_t CC1101_FOCCFG = 0x19;
inline constexpr uint8_t CC1101_BSCFG = 0x1A;
inline constexpr uint8_t CC1101_AGCCTRL2 = 0x1B;
inline constexpr uint8_t CC1101_AGCCTRL1 = 0x1C;
inline constexpr uint8_t CC1101_AGCCTRL0 = 0x1D;
inline constexpr uint8_t CC1101_FREND1 = 0x21;
inline constexpr uint8_t CC1101_FREND0 = 0x22;
inline constexpr uint8_t CC1101_FSCAL3 = 0x23;
inline constexpr uint8_t CC1101_FSCAL2 = 0x24;
inline constexpr uint8_t CC1101_FSCAL1 = 0x25;
inline constexpr uint8_t CC1101_FSCAL0 = 0x26;
inline constexpr uint8_t CC1101_TEST2 = 0x2C;
inline constexpr uint8_t CC1101_TEST1 = 0x2D;
inline constexpr uint8_t CC1101_TEST0 = 0x2E;
inline constexpr uint8_t CC1101_LQI = 0x33;
inline constexpr uint8_t CC1101_RSSI = 0x34;
inline constexpr uint8_t CC1101_RXFIFO = 0x3F;
inline constexpr uint8_t CC1101_PKTSTATUS = 0x38;
inline constexpr uint8_t CC1101_RXBYTES = 0x3B;
inline constexpr uint8_t CC1101_PATABLE = 0x3E;

inline constexpr uint8_t CC1101_SRES = 0x30;
inline constexpr uint8_t CC1101_SCAL = 0x33;
inline constexpr uint8_t CC1101_SRX = 0x34;
inline constexpr uint8_t CC1101_STX = 0x35;
inline constexpr uint8_t CC1101_SIDLE = 0x36;
inline constexpr uint8_t CC1101_SFRX = 0x3A;
inline constexpr uint8_t CC1101_SFTX = 0x3B;

enum class TXMode : uint8_t {
  CONTINUOUS_BURST = 0,
  REPEATED_STROBE = 1,
  CLEAN_TIMING_TEST = 2,
  PROFLAME_PWM_SYMBOLS = 3,
  PROFLAME_NATIVE_GROUPS = 4,
};

enum class TestPatternMode : uint8_t {
  ALTERNATING_OOK = 0,
  CARRIER_ON = 1,
  CARRIER_OFF = 2,
};

enum class AsyncTxDataPin : uint8_t {
  GDO0 = 0,
  GDO2 = 1,
};

enum class NativeGroupTimingProfile : uint8_t {
  YARDSTICK_COMPAT = 0,
  NATIVE_REMOTE = 1,
};

enum class NativeGroupRepeatBoundaryMode : uint8_t {
  CONTINUOUS_TX = 0,
  REENTER_TX = 1,
};

/// Transmit timing and optional debug trace for one CC1101 TX operation.
///
/// This is a passive diagnostics container owned by the caller. RadioCC1101
/// writes summary fields for every build. When `PROFLAME2_TEMBED_TX_DEBUG` is
/// enabled it also records bounded sample traces; those traces are diagnostic
/// only and must not become protocol truth.
struct TXTimingDiagnostics {
#if PROFLAME2_TEMBED_TX_DEBUG
  static constexpr size_t BIT_TIMING_SAMPLE_CAPACITY = 64;
  static constexpr size_t REPEAT_TRACE_CAPACITY = 20;

  struct BitTimingSample {
    uint32_t bit_index{0};
    uint8_t bit_value{0};
    int64_t target_offset_us{0};
    int64_t actual_offset_us{0};
    uint32_t timing_error_us{0};
  };

  struct RepeatTimingSample {
    uint8_t repeat_index{0};
    uint8_t repeat_count{0};
    int64_t repeat_start_us{0};
    int64_t previous_repeat_end_us{0};
    int64_t first_bit_us{0};
    int64_t repeat_end_us{0};
    uint32_t actual_gap_from_previous_end_to_first_bit_us{0};
    uint32_t setup_duration_before_first_bit_us{0};
    uint32_t frame_duration_us{0};
    uint64_t total_burst_duration_us{0};
    uint8_t strobe_sidle_status{0};
    uint8_t strobe_sftx_status{0};
    uint8_t strobe_stx_status{0};
    uint8_t marcstate_after_enter_tx{0};
    uint8_t marcstate_after_repeat{0};
  };

  uint8_t first_bits_count{0};
  std::array<char, 17> first_bits{};
#endif

  uint32_t payload_bits{0};
  uint32_t bit_period_us{0};
  uint32_t repeat_gap_us{0};
  uint32_t inter_repeat_gap_measured_us{0};
  uint32_t inter_repeat_gap_min_us{std::numeric_limits<uint32_t>::max()};
  uint32_t inter_repeat_gap_max_us{0};
  uint64_t inter_repeat_gap_total_us{0};
  uint32_t inter_repeat_gap_samples{0};
  uint32_t first_rising_edge_late_min_us{std::numeric_limits<uint32_t>::max()};
  uint32_t first_rising_edge_late_max_us{0};
  uint64_t first_rising_edge_late_total_us{0};
  uint32_t first_rising_edge_late_samples{0};
  uint64_t total_burst_duration_us{0};
  uint32_t min_repeat_duration_us{std::numeric_limits<uint32_t>::max()};
  uint32_t max_repeat_duration_us{0};
  uint64_t total_repeat_duration_us{0};
  uint32_t bit_timing_error_min_us{std::numeric_limits<uint32_t>::max()};
  uint32_t bit_timing_error_max_us{0};
  uint64_t bit_timing_error_total_us{0};
  uint32_t bit_timing_samples{0};
#if PROFLAME2_TEMBED_TX_DEBUG
  std::array<BitTimingSample, BIT_TIMING_SAMPLE_CAPACITY> bit_timing_trace{};
  uint8_t bit_timing_trace_count{0};
  std::array<RepeatTimingSample, REPEAT_TRACE_CAPACITY> repeat_timing_trace{};
  uint8_t repeat_timing_trace_count{0};
#endif
};

/// Raw CC1101 FIFO probe result for manual/debug RX acquisition.
///
/// The byte buffer is a bounded snapshot of what the radio FIFO produced under
/// one receive configuration. It is not decoded or semantically comparable by
/// itself; callers must run the Proflame2 candidate scanner before promoting a
/// packet to semantic state.
struct RXFifoProbeResult {
  static constexpr size_t MAX_BYTES = 512;

  uint32_t frequency_hz{0};
  uint32_t data_rate_bps{0};
  uint32_t requested_duration_ms{0};
  uint32_t elapsed_ms{0};
  uint32_t started_tick_ms{0};
  uint32_t completed_tick_ms{0};
  uint32_t poll_count{0};
  uint16_t byte_count{0};
  bool buffer_full{false};
  bool rx_fifo_overflow{false};
  uint8_t rxbytes_max{0};
  uint8_t rxbytes_final{0};
  uint8_t marcstate_before{0};
  uint8_t marcstate_after_config{0};
  uint8_t marcstate_after_rx{0};
  uint8_t marcstate_after_idle{0};
  uint8_t rssi_raw{0};
  uint8_t lqi_raw{0};
  uint8_t pktstatus{0};
  uint8_t partnum{0};
  uint8_t version{0};
  uint8_t mdmcfg4{0};
  uint8_t mdmcfg3{0};
  uint8_t mdmcfg2{0};
  uint8_t pktctrl1{0};
  uint8_t pktctrl0{0};
  uint8_t sync1{0};
  uint8_t sync0{0};
  uint8_t agcctrl2{0};
  uint8_t agcctrl1{0};
  uint8_t agcctrl0{0};
  std::array<uint8_t, MAX_BYTES> bytes{};
};

/// Low-level CC1101 register/SPI driver used by the T-Embed component.
///
/// Ownership and lifetime:
/// - ESPHome owns GPIO/SPI objects; this class stores borrowed GPIO pointers.
/// - Public TX/RX methods are expected to run from the component loop or HA
///   service path, not from ISR context.
///
/// RF boundaries:
/// - TX methods emit caller-supplied payload bits; they do not encode
///   Proflame2 frames.
/// - `rx_fifo_probe()` captures raw FIFO bytes only; it does not decode or
///   publish HA events.
/// - `set_idle()` returns the CC1101 to IDLE and is used by higher layers to
///   enforce TX/RX mutual exclusion.
class RadioCC1101 : public spi::SPIDevice<spi::BIT_ORDER_MSB_FIRST, spi::CLOCK_POLARITY_LOW, spi::CLOCK_PHASE_LEADING,
                                          spi::DATA_RATE_4MHZ> {
public:
  /// Configure the CC1101 for asynchronous ASK/OOK TX on the selected GDO pin.
  bool setup_async_ook_tx(GPIOPin* gdo0_pin, GPIOPin* gdo2_pin, uint32_t frequency_hz, uint32_t data_rate_bps,
                          std::string& error);
  /// Transmit one prepared payload using the selected Proflame2 timing mode.
  ///
  /// `payload_bit_length` may be shorter than `length * 8` when the last byte
  /// contains padding. Timing diagnostics are written into `timing`.
  bool transmit_async_ook(const uint8_t* payload, size_t length, uint32_t payload_bit_length, uint8_t repeat_count,
                          uint32_t repeat_gap_us, TXMode tx_mode, NativeGroupTimingProfile native_group_timing_profile,
                          NativeGroupRepeatBoundaryMode native_group_repeat_boundary_mode, uint32_t pre_burst_low_us,
                          uint32_t pre_frame_low_us, uint32_t post_frame_idle_gap_us, uint32_t& elapsed_ms,
                          TXTimingDiagnostics& timing, std::string& error);
  /// Emit a diagnostic RF pattern. This is not a Proflame2 command.
  bool transmit_test_pattern_async_ook(TestPatternMode mode, uint32_t duration_ms, uint32_t period_us,
                                       uint32_t& elapsed_ms, TXTimingDiagnostics& timing, std::string& error);
  /// Drain one bounded TX debug trace item for deferred logging outside timing-sensitive work.
  bool drain_debug_tx_diagnostics(const TXTimingDiagnostics& timing, TXMode tx_mode, uint8_t& phase,
                                  uint8_t& repeat_index, uint8_t& bit_index) const;
  /// Put the CC1101 into IDLE without changing higher-level component state.
  void set_idle();
  /// Capture a bounded raw FIFO byte window for diagnostics/manual probing.
  bool rx_fifo_probe(uint32_t frequency_hz, uint32_t data_rate_bps, uint32_t duration_ms, RXFifoProbeResult& result,
                     std::string& error);
  void set_async_tx_data_pin(AsyncTxDataPin pin) {
    this->async_tx_data_pin_ = pin;
  }
  void log_rf_output_path(GPIOPin* gdo0_pin, GPIOPin* gdo2_pin, TXMode tx_mode, uint32_t payload_length,
                          uint32_t payload_bit_length, uint8_t repeat_count, uint32_t repeat_gap_us);
  void log_runtime_register_snapshot(const char* stage);

  bool is_radio_initialized() const {
    return this->initialized_;
  }
  uint8_t read_partnum() {
    return this->read_status_register_(0x30);
  }
  uint8_t read_version() {
    return this->read_status_register_(0x31);
  }
  uint8_t read_marcstate() {
    return static_cast<uint8_t>(this->read_status_register_(0x35) & 0x1F);
  }

protected:
  bool write_register_(uint8_t address, uint8_t value);
  uint8_t read_register_(uint8_t address);
  uint8_t read_status_register_(uint8_t address);
  void read_burst_register_(uint8_t address, uint8_t* data, size_t length);
  void write_burst_register_(uint8_t address, const uint8_t* data, size_t length);
  uint8_t strobe_(uint8_t command);
  bool apply_async_ook_registers_(std::string& error, bool log_config = true);
  bool enter_tx_mode_(std::string& error);
  void log_register_snapshot_();
  static uint32_t wait_until_(int64_t target_us);
#if PROFLAME2_TEMBED_TX_DEBUG
  static void capture_first_bits_(TXTimingDiagnostics& timing, const uint8_t* payload, uint32_t payload_bit_length);
  static void log_bit_timing_trace_(const TXTimingDiagnostics& timing);
  static void log_repeat_timing_trace_(const TXTimingDiagnostics& timing, TXMode tx_mode);
#endif
  static const char* tx_mode_to_string_(TXMode tx_mode);
  static const char* test_pattern_mode_to_string_(TestPatternMode mode);
  static const char* async_tx_data_pin_to_string_(AsyncTxDataPin pin);
  static const char* native_group_timing_profile_to_string_(NativeGroupTimingProfile profile);
  static const char* native_group_repeat_boundary_mode_to_string_(NativeGroupRepeatBoundaryMode mode);
  static const char* marcstate_to_string_(uint8_t marcstate);
  static uint32_t compute_frequency_word_(uint32_t frequency_hz);
  static void compute_drate_registers_(uint32_t data_rate_bps, uint8_t& mdmcfg4, uint8_t& mdmcfg3);
  GPIOPin* async_tx_pin_() const;

  GPIOPin* gdo0_pin_{nullptr};
  GPIOPin* gdo2_pin_{nullptr};
  AsyncTxDataPin async_tx_data_pin_{AsyncTxDataPin::GDO0};
  bool initialized_{false};
  uint32_t frequency_hz_{0};
  uint32_t data_rate_bps_{0};
  uint8_t last_sidled_status_{0};
  uint8_t last_sftx_status_{0};
  uint8_t last_stx_status_{0};
};

} // namespace proflame2_tembed
} // namespace esphome
