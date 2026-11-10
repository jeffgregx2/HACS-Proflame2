// Execute production tuning and RX restoration against the mock SPI bus.
#include "radio_cc1101.h"
#include "rf_band.h"
#include "esphome/core/log.h"
#include "esphome/core/hal.h"
#include <cassert>
#include <string>
#include <vector>

using namespace esphome;
using namespace esphome::proflame2_tembed;
static constexpr const char* TAG = "test";
static void delay(uint32_t ms) {
  test_clock_us += ms * 1000;
}

namespace esphome {
namespace proflame2_tembed {
class Proflame2TEmbedComponent : public RadioCC1101 {
public:
  uint32_t rx_frequency_hz_{0};
  uint32_t data_rate_bps_{2400};
  int32_t tx_frequency_offset_hz_{0};
  bool rx_rmt_pulse_capture_enabled_{false}, rx_rmt_pulse_paused_for_tx_{false};
  bool rx_fifo_capture_enabled_{false}, rx_fifo_paused_for_tx_{false}, rx_fifo_capture_configured_{false};
  std::string rx_fifo_profile_{"rfcat_fixed_none_rfcat_wide"};
  struct Fifo {
    void set_radio_status(uint8_t, uint8_t, uint8_t, uint8_t) {}
    uint8_t rssi_raw_last() const {
      return 0;
    }
    uint8_t lqi_raw_last() const {
      return 0;
    }
    uint8_t pktstatus_last() const {
      return 0;
    }
  } rx_fifo_;
  bool configure_rmt_pulse_capture_mode_(std::string& error);
  bool configure_rx_fifo_capture_mode_(std::string& error);
  void pause_rx_for_tx_();
  void restore_rx_after_tx_if_needed_();
  void reset_rx_fifo_rolling_capture_(uint32_t) {}
  void increment_rx_suppressed_for_tx_() {}
  const char* rx_fifo_profile_name_() const {
    return rx_fifo_profile_.c_str();
  }
  void set_rmt_pulse_capture_enabled_(bool value) {
    std::string error;
    // Only RMT peripheral ownership is mocked; the radio configurations below
    // and the pause/resume entrypoints are copied from the production component.
    if (value) {
      assert(configure_rmt_pulse_capture_mode_(error));
      strobe_(CC1101_SRX);
    } else {
      set_idle();
      assert(apply_async_ook_registers_(error, false));
    }
    rx_rmt_pulse_capture_enabled_ = value;
  }
};

// INSERT_PRODUCTION_TUNING_METHODS
} // namespace proflame2_tembed
} // namespace esphome

static uint32_t frequency_word(uint32_t hz) {
  return (static_cast<uint64_t>(hz) << 16U) / 26000000U;
}
static uint32_t programmed_frequency() {
  return (static_cast<uint32_t>(spi::registers[0x0D]) << 16U) | (static_cast<uint32_t>(spi::registers[0x0E]) << 8U) |
         spi::registers[0x0F];
}

int main(int argc, char** argv) {
  assert(argc == 3);
  std::vector<uint8_t> payload;
  const std::string hex = argv[1];
  for (size_t i = 0; i < hex.size(); i += 2)
    payload.push_back(static_cast<uint8_t>(std::stoul(hex.substr(i, 2), nullptr, 16)));
  const uint32_t bits = static_cast<uint32_t>(std::stoul(argv[2]));
  for (const auto band : {RFBand::BAND_315, RFBand::BAND_433}) {
    for (const int32_t offset : {0, 60000, -60000, 100000, -100000}) {
      for (const bool rmt : {false, true}) {
        test_clock_us = 0;
        spi::allow_radio_operations = true;
        spi::registers.fill(0);
        spi::tx_never_ready = false;
        spi::idle_never_ready = false;
        Proflame2TEmbedComponent radio;
        InternalGPIOPin pin;
        RFBandConfiguration config;
        uint32_t tx_hz;
        assert(resolve_rf_band_configuration(band, config));
        assert(resolve_tx_frequency(band, offset, tx_hz));
        radio.rx_frequency_hz_ = config.frequency_hz;
        radio.tx_frequency_offset_hz_ = offset;
        std::string error;
        assert(radio.setup_async_ook_tx(&pin, nullptr, tx_hz, 2400, error));
        assert(programmed_frequency() == frequency_word(tx_hz));
        radio.rx_fifo_capture_enabled_ = !rmt;
        if (rmt)
          radio.set_rmt_pulse_capture_enabled_(true);
        else
          assert(radio.configure_rx_fifo_capture_mode_(error));
        assert(programmed_frequency() == frequency_word(config.frequency_hz));
        // Normal TX and the later retry traverse the same pause/restore cycle.
        for (int attempt = 0; attempt < 2; ++attempt) {
          radio.pause_rx_for_tx_();
          assert(programmed_frequency() == frequency_word(tx_hz));
          assert(spi::registers[0x0C] == 0); // FSCTRL0 is not used as a shared RX/TX offset.
          uint32_t elapsed;
          TXTimingDiagnostics timing;
          assert(radio.transmit_async_ook(payload.data(), payload.size(), bits, 5, 0, TXMode::PROFLAME_NATIVE_GROUPS,
                                          NativeGroupTimingProfile::NATIVE_REMOTE,
                                          NativeGroupRepeatBoundaryMode::CONTINUOUS_TX, 834, 0, 0, elapsed, timing,
                                          error, 20000));
          assert(programmed_frequency() == frequency_word(tx_hz));
          radio.restore_rx_after_tx_if_needed_();
          assert(programmed_frequency() == frequency_word(config.frequency_hz));
          assert(radio.rx_frequency_hz_ == config.frequency_hz);
        }
        // Rejected TX and queue-admission failure still restore the baseline RX carrier.
        radio.pause_rx_for_tx_();
        uint32_t elapsed;
        TXTimingDiagnostics timing;
        assert(!radio.transmit_async_ook(
            nullptr, 0, bits, 5, 0, TXMode::PROFLAME_NATIVE_GROUPS, NativeGroupTimingProfile::NATIVE_REMOTE,
            NativeGroupRepeatBoundaryMode::CONTINUOUS_TX, 834, 0, 0, elapsed, timing, error, 20000));
        radio.restore_rx_after_tx_if_needed_();
        assert(programmed_frequency() == frequency_word(config.frequency_hz));
        radio.pause_rx_for_tx_();
        radio.restore_rx_after_tx_if_needed_();
        assert(programmed_frequency() == frequency_word(config.frequency_hz));
      }
    }
  }
}
