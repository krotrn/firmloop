#pragma once
#include "Driver.h"

// Relay module driving the 5V fan, active low (LOW = relay on, per manifest active_low).
// Channel: fan (actuator). Off = pin HIGH, set before the pin becomes an output so the relay
// never clicks at boot.
class RelayDriver : public fl::Driver {
 public:
  explicit RelayDriver(uint8_t signal) : pin_(signal) {}
  void begin() override;
  void apply(uint32_t now_ms) override;
  void neutral() override;

 private:
  void write(bool on);
  uint8_t pin_;
  bool known_ = false;
  bool state_ = false;
};
