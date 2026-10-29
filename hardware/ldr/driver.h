#pragma once
#include "Driver.h"

// LDR in a divider with 10k, read on an ADC1 pin (12-bit, 0..4095, higher = brighter; wire the
// LDR on the 3V3 side). Channel: light. Sampled every 100 ms, mean of 4 reads.
class LdrDriver : public fl::Driver {
 public:
  explicit LdrDriver(uint8_t signal) : pin_(signal) {}
  void begin() override;
  void poll(uint32_t now_ms) override;

 private:
  uint8_t pin_;
  fl::IntervalGate gate_;
};
