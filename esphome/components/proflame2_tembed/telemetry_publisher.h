// Sensor/text-sensor publication helpers.
//
// The component owns source state and cache storage. This helper owns only the
// publish-if-changed mechanics so HA entity behavior stays centralized.

#pragma once

#include <cstdint>
#include <string>

namespace esphome {
namespace sensor {
class Sensor;
}
namespace text_sensor {
class TextSensor;
}
namespace proflame2_tembed {

class TelemetryPublisher {
public:
  static void publish_text_if_changed(text_sensor::TextSensor* sensor, std::string* cache, const std::string& value);
  static void publish_uint32_if_changed(sensor::Sensor* sensor, uint32_t* cache, uint32_t value);
  static void publish_uint8_if_changed(sensor::Sensor* sensor, uint8_t* cache, uint8_t value);
  static void publish_float_if_changed(sensor::Sensor* sensor, float* cache, float value, float epsilon = 0.1f);
};

} // namespace proflame2_tembed
} // namespace esphome
