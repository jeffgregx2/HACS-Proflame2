"""Execute the production sensor publication with effective repeat-count values."""

import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "esphome/components/proflame2_tembed"


def test_repeat_count_sensor_reports_effective_value_without_changing_request_validation(tmp_path):
    component = (COMPONENT / "proflame2_tembed.cpp").read_text()
    start = component.index("TelemetryPublisher::publish_uint8_if_changed(this->tx_repeat_count_sensor_")
    publication = component[start : component.index(";", start) + 1]
    helper = (COMPONENT / "telemetry_publisher.cpp").read_text()
    start = helper.index("void TelemetryPublisher::publish_uint8_if_changed(")
    helper = helper[start : helper.index("void TelemetryPublisher::publish_float_if_changed", start)]
    source = tmp_path / "repeat_count_sensor.cpp"
    source.write_text(r"""
#include <cassert>
#include <cstdint>
#include <vector>
#include "telemetry_publisher.h"
#include "tx_controller.h"
namespace esphome {
namespace sensor {
class Sensor {
public:
  std::vector<float> values;
  void publish_state(float value) { values.push_back(value); }
};
}
namespace proflame2_tembed {
// INSERT_PUBLISH_HELPER
}
}
using namespace esphome;
using namespace esphome::proflame2_tembed;
class Bench {
public:
  uint8_t tx_repeat_count_{5};
  uint8_t diagnostic_repeat_count_override_{0};
  uint8_t published_tx_repeat_count_cache_{255};
  sensor::Sensor* tx_repeat_count_sensor_{nullptr};
  void publish() { /* INSERT_PUBLICATION */ }
};
int main() {
  Bench bench;
  bench.publish(); // Optional/unconfigured sensor must remain safe.
  sensor::Sensor sensor;
  bench.tx_repeat_count_sensor_ = &sensor;
  for (const uint8_t override_count : {0, 10, 8, 1, 20, 0}) {
    bench.diagnostic_repeat_count_override_ = override_count;
    const uint8_t expected = override_count == 0 ? 5 : override_count;
    const size_t before = sensor.values.size();
    bench.publish();
    assert(sensor.values.back() == expected);
    assert(sensor.values.size() == before + 1);
    bench.publish();
    assert(sensor.values.size() == before + 1); // No repeated publication noise.
    assert(bench.tx_repeat_count_ == 5); // Telemetry does not change the contract.
    TxValidationConfig config{5, override_count, 0};
    const auto request = TxController::validate_payload_request("aa", 8, 5, config);
    assert(request.accepted());
    assert(request.prepared.effective_repeat_count == expected);
    const auto mismatch = TxController::validate_payload_request("aa", 8, 4, config);
    assert(mismatch.accepted() == (override_count != 0));
  }
  bench.tx_repeat_count_ = 12;
  bench.publish();
  assert(sensor.values.back() == 12); // No override falls back to configured count.
}
""".replace("// INSERT_PUBLISH_HELPER", helper).replace("/* INSERT_PUBLICATION */", publication))
    compiler = shutil.which("g++")
    assert compiler
    executable = tmp_path / "repeat_count_sensor"
    subprocess.run(
        [
            compiler,
            "-std=c++17",
            "-Wall",
            "-Wextra",
            "-Werror",
            f"-I{COMPONENT}",
            str(source),
            str(COMPONENT / "tx_controller.cpp"),
            "-o",
            str(executable),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run([str(executable)], check=True, capture_output=True, text=True)
