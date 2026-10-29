#pragma once
#include "Driver.h"

// HC-SR501 PIR: output HIGH = motion. Channel: motion. Sampled every 50 ms (the sensor holds
// its output for seconds itself, so no extra filtering).
class PirDriver : public fl::Driver {
 public:
  explicit PirDriver(uint8_t signal) : pin_(signal) {}
  void begin() override;
  void poll(uint32_t now_ms) override;

 private:
  uint8_t pin_;
  fl::IntervalGate gate_;
};
