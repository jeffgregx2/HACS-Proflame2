#include "wifi_diagnostics.h"
#include "esphome/core/log.h"
#include <cassert>
#include <cstdint>
#include <string>

namespace esphome {
static uint32_t test_millis = 0;
static uint32_t millis() {
  return test_millis;
}
namespace proflame2_tembed {
class Proflame2TEmbedComponent {
public:
  WifiDiagnostics wifi_diagnostics_;
  struct {
    bool wifi_connected{false}, wifi_rssi_valid{false};
    float wifi_rssi_dbm{0};
    unsigned wifi_bars{0};
  } display_;
  unsigned dirty_count{0};
  bool retry{true};
  void mark_display_dirty_() {
    ++dirty_count;
  }
  void set_wifi_connected(bool value);
  const WifiDiagnostics& get_wifi_diagnostics() const {
    return wifi_diagnostics_;
  }
  bool is_delayed_retransmission_enabled() const {
    return retry;
  }
  uint32_t get_pre_tx_settle_delay_us() const {
    return 20000;
  }
};
// INSERT_PRODUCTION_WIFI_METHOD
} // namespace proflame2_tembed
} // namespace esphome

using esphome::proflame2_tembed::Proflame2TEmbedComponent;
using esphome::proflame2_tembed::WifiDiagnostics;
static Proflame2TEmbedComponent proflame2_radio;
static struct {
  float state{125};
} proflame2_uptime;
static struct {
  float state{-80}; // Summary must use the fresh raw sample, not a stale state.
  float get_raw_state() const {
    return -47;
  }
} proflame2_wifi_rssi;
static struct {
  std::string state{"AA:BB:CC:DD:EE:FF"};
} proflame2_wifi_bssid;
static struct {
  std::string state{"Home WiFi"};
} proflame2_wifi_ssid;
static struct {
  std::string state{"Software Reset CPU"};
} proflame2_reset_reason;
#define id(value) value
static void api_summary() {
  // INSERT_PRODUCTION_SUMMARY
}
using esphome::millis;
static void wifi_disconnect() {
  // INSERT_PRODUCTION_DISCONNECT
}

int main() {
  WifiDiagnostics wifi;
  wifi.observe(false, 0); // Startup failures before first association aren't drops.
  wifi.observe(false, 10);
  assert(!wifi.connected() && wifi.disconnect_count() == 0 && wifi.reconnect_count() == 0);
  wifi.observe(true, 20);
  wifi.observe(true, 21);
  assert(wifi.connected() && wifi.disconnect_count() == 0 && wifi.reconnect_count() == 0);
  wifi.observe(false, 100);
  wifi.observe(false, 150);
  assert(wifi.disconnect_count() == 1 && wifi.reconnect_count() == 0);
  wifi.observe(true, 600);
  wifi.observe(true, 700);
  assert(wifi.reconnect_count() == 1 && wifi.last_outage_ms() == 500);
  wifi.observe(false, UINT32_MAX - 99);
  wifi.observe(true, 100); // Outage straddles millis() wrap.
  assert(wifi.disconnect_count() == 2 && wifi.reconnect_count() == 2 && wifi.last_outage_ms() == 200);
  const WifiDiagnostics rebooted;
  assert(!rebooted.connected() && rebooted.disconnect_count() == 0 && rebooted.reconnect_count() == 0);
  assert(rebooted.last_outage_ms() == 0);

  // Execute the real display/diagnostic Wi-Fi callback, not a second policy.
  proflame2_radio.set_wifi_connected(true);
  proflame2_radio.display_.wifi_rssi_valid = true;
  proflame2_radio.display_.wifi_rssi_dbm = -47;
  proflame2_radio.display_.wifi_bars = 4;
  esphome::test_millis = 1000;
  proflame2_radio.set_wifi_connected(false);
  proflame2_radio.set_wifi_connected(false);
  assert(!proflame2_radio.display_.wifi_rssi_valid && proflame2_radio.display_.wifi_bars == 0);
  assert(proflame2_radio.display_.wifi_rssi_dbm == 0);
  assert(proflame2_radio.dirty_count == 2);
  esphome::test_millis = 4500;
  proflame2_radio.set_wifi_connected(true);
  const auto& stats = proflame2_radio.get_wifi_diagnostics();
  assert(stats.disconnect_count() == 1 && stats.reconnect_count() == 1 && stats.last_outage_ms() == 3500);
  // Compile/execute the actual YAML log lambda with printf format checking.
  api_summary();
  assert(trace_logs.size() == 1);
  const auto summary = trace_logs.back();
  assert(summary.find("connected=YES disconnects=1 reconnects=1 last_outage_ms=3500") != std::string::npos);
  assert(summary.find("uptime_s=125 ssid='Home WiFi' bssid=AA:BB:CC:DD:EE:FF rssi_dbm=-47") != std::string::npos);
  assert(summary.find("last_boot_reason='Software Reset CPU' retransmission=ON pre_tx_settle_ms=20.0") !=
         std::string::npos);
  proflame2_radio.retry = false;
  api_summary(); // An API reconnect alone does not create a Wi-Fi edge.
  assert(trace_logs.size() == 2 && trace_logs.back().find("retransmission=OFF") != std::string::npos);
  assert(stats.disconnect_count() == 1 && stats.reconnect_count() == 1 && stats.last_outage_ms() == 3500);
  esphome::test_millis = 8000;
  wifi_disconnect();
  assert(!stats.connected() && stats.disconnect_count() == 2 && stats.reconnect_count() == 1);
  assert(trace_logs.size() == 3);
  assert(trace_logs.back().find(
             "WiFi disconnected disconnects=2 reconnects=1 uptime_ms=8000 last_rssi_dbm=-47 retransmission=OFF") !=
         std::string::npos);
}
