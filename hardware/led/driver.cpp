#include "driver.h"

#include <Arduino.h>

void LedDriver::write(bool on) {
  digitalWrite(pin_, on ? HIGH : LOW);
  state_ = on;
  known_ = true;
}

void LedDriver::begin() {
  digitalWrite(pin_, LOW);  // set the level before enabling the output: no glitch
  pinMode(pin_, OUTPUT);
}

void LedDriver::neutral() { write(false); }

void LedDriver::apply(uint32_t now_ms) {
  (void)now_ms;
  bool on = fl::actuatorOn("led");
  if (!known_ || on != state_) write(on);
}
