#pragma once
// Base class for hardware drivers (hardware/<id>/driver.h derives from this).
// Drivers read/write physical parts; user logic never sees them, only channels (FirmLoop.h).
// No heap allocation after begin(); no exceptions, no RTTI.
#include <stdint.h>

namespace fl {

// ---- Driver-facing runtime API (implemented in FirmLoop.cpp, ESP32 only). Drivers talk to the
// channel table through these; user code never does (it uses FirmLoop.h).
void publishFloat(const char* channel, float v);    // sensor drivers: store a fresh reading
void publishInt(const char* channel, int32_t v);
void publishBool(const char* channel, bool v);
bool actuatorOn(const char* channel);               // effective value: override wins over setBool
const char* displayText(uint8_t row);               // current text of row 0..3 (<= 21 chars)
uint32_t displayRevision();                         // changes whenever any row changes
void reportError(const char* code, const char* msg);  // emits an `err` line (rate-limited)

// Sample-interval helper: due() is true on the first call, then once per `interval_ms`.
class IntervalGate {
 public:
  bool due(uint32_t now_ms, uint32_t interval_ms) {
    if (started_ && (uint32_t)(now_ms - last_) < interval_ms) return false;
    started_ = true;
    last_ = now_ms;
    return true;
  }

 private:
  uint32_t last_ = 0;
  bool started_ = false;
};

class Driver {
 public:
  virtual ~Driver() {}
  // Configure pins / start the part. Called once from runtime_begin().
  virtual void begin() {}
  // Sensor drivers: sample the part and publish into the channel table when due.
  virtual void poll(uint32_t now_ms) { (void)now_ms; }
  // Actuator/display drivers: apply the channel table values to the part.
  virtual void apply(uint32_t now_ms) { (void)now_ms; }
  // Put the part in its neutral state (called before user_setup and when leaving test mode).
  virtual void neutral() {}
};

}  // namespace fl
