#pragma once
// Base class for hardware drivers (hardware/<id>/driver.h derives from this).
// Drivers read/write physical parts; user logic never sees them, only channels (FirmLoop.h).
// No heap allocation after begin(); no exceptions, no RTTI.
#include <stdint.h>

namespace fl {

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
