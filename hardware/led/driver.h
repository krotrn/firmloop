#pragma once
#include "Driver.h"

// LED, HIGH = on. Channel: led (actuator). Follows the effective channel value (override wins).
class LedDriver : public fl::Driver {
 public:
  explicit LedDriver(uint8_t signal) : pin_(signal) {}
  void begin() override;
  void apply(uint32_t now_ms) override;
  void neutral() override;

 private:
  void write(bool on);
  uint8_t pin_;
  bool known_ = false;
  bool state_ = false;
};
