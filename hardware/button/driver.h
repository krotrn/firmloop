#pragma once
#include "Driver.h"

// Push button to GND with the internal pull-up: LOW = pressed. Channel: button (pressed = 1).
// Sampled every 20 ms; a change is accepted after two equal samples (about 40 ms debounce).
class ButtonDriver : public fl::Driver {
 public:
  explicit ButtonDriver(uint8_t signal) : pin_(signal) {}
  void begin() override;
  void poll(uint32_t now_ms) override;

 private:
  uint8_t pin_;
  bool last_ = false;     // previous raw sample
  bool stable_ = false;   // debounced state
  fl::IntervalGate gate_;
};
