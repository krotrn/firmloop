#pragma once
// Shared by the ESP32 core (fl_core.h) and the native fake HAL so both accept the same sensor
// values and print byte-identical `s` / `a` / `d` state fragments (D-063).
#include <math.h>
#include <stdint.h>

#include "FirmLoopInternal.h"
#include "fl_json.h"

namespace fl {

// A sensor value must be finite and within +-1e9: no NaN/Inf ever reaches a snapshot, and the
// (int32_t) cast of an Int channel can never overflow.
constexpr double MAX_SENSOR_ABS = 1e9;
inline bool validSensorNumber(double v) { return isfinite(v) && fabs(v) <= MAX_SENSOR_ABS; }

// Writes `"s":{..},"a":{..},"d":[..]` (no enclosing braces, no leading comma) into an open object.
// `A` supplies: float f(i), int32_t iv(i), bool b(i) (sensor storage), bool eff(i) (effective
// actuator state), const char* row(r).
template <class A>
void writeState(JsonWriter& w, const A& a) {
  w.key("s"); w.beginObj();
  for (uint8_t i = 0; i < CHANNEL_COUNT; ++i) {
    const ChannelDef& d = CHANNELS[i];
    if (d.kind != Kind::Sensor) continue;
    w.key(d.id);
    if (d.type == Type::Float) w.f32(a.f(i), 1);
    else if (d.type == Type::Int) w.i32(a.iv(i));
    else w.i32(a.b(i) ? 1 : 0);
  }
  w.endObj();
  w.key("a"); w.beginObj();
  for (uint8_t i = 0; i < CHANNEL_COUNT; ++i) {
    if (CHANNELS[i].kind != Kind::Actuator) continue;
    w.key(CHANNELS[i].id);
    w.i32(a.eff(i) ? 1 : 0);
  }
  w.endObj();
  w.key("d"); w.beginArr();
  for (uint8_t r = 0; r < DISPLAY_ROWS; ++r) w.str(a.row(r));
  w.endArr();
}

}  // namespace fl
