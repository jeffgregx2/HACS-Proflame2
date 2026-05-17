#include "telemetry_publisher.h"

#include <cmath>

#include "esphome/components/sensor/sensor.h"
#include "esphome/components/text_sensor/text_sensor.h"

namespace esphome {
namespace proflame2_tembed {

void TelemetryPublisher::publish_text_if_changed(text_sensor::TextSensor* sensor, std::string* cache,
                                                 const std::string& value) {
  if (sensor == nullptr || cache == nullptr) {
    return;
  }
  if (*cache == value) {
    return;
  }
  *cache = value;
  sensor->publish_state(value);
}

void TelemetryPublisher::publish_uint32_if_changed(sensor::Sensor* sensor, uint32_t* cache, uint32_t value) {
  if (sensor == nullptr || cache == nullptr) {
    return;
  }
  if (*cache == value) {
    return;
  }
  *cache = value;
  sensor->publish_state(static_cast<float>(value));
}

void TelemetryPublisher::publish_uint8_if_changed(sensor::Sensor* sensor, uint8_t* cache, uint8_t value) {
  if (sensor == nullptr || cache == nullptr) {
    return;
  }
  if (*cache == value) {
    return;
  }
  *cache = value;
  sensor->publish_state(static_cast<float>(value));
}

void TelemetryPublisher::publish_float_if_changed(sensor::Sensor* sensor, float* cache, float value, float epsilon) {
  if (sensor == nullptr || cache == nullptr) {
    return;
  }
  if (!std::isnan(*cache) && std::fabs(*cache - value) <= epsilon) {
    return;
  }
  *cache = value;
  sensor->publish_state(value);
}

} // namespace proflame2_tembed
} // namespace esphome
