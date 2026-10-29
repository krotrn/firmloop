#include "driver.h"

#include <Arduino.h>

void RelayDriver::write(bool on) {
  digitalWrite(pin_, on ? LOW : HIGH);  // active low
  state_ = on;
  known_ = true;
}

void RelayDriver::begin() {
  digitalWrite(pin_, HIGH);  // off, before enabling the output
  pinMode(pin_, OUTPUT);
}

void RelayDriver::neutral() { write(false); }

void RelayDriver::apply(uint32_t now_ms) {
  (void)now_ms;
  bool on = fl::actuatorOn("fan");
  if (!known_ || on != state_) write(on);
}
