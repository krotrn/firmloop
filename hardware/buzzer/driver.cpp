#include "driver.h"

#include <Arduino.h>

void BuzzerDriver::write(bool on) {
  digitalWrite(pin_, on ? HIGH : LOW);
  state_ = on;
  known_ = true;
}

void BuzzerDriver::begin() {
  digitalWrite(pin_, LOW);  // set the level before enabling the output: no glitch
  pinMode(pin_, OUTPUT);
}

void BuzzerDriver::neutral() { write(false); }

void BuzzerDriver::apply(uint32_t now_ms) {
  (void)now_ms;
  bool on = fl::actuatorOn("buzzer");
  if (!known_ || on != state_) write(on);
}
