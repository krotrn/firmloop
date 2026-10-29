#include "driver.h"

#include <Arduino.h>

void ButtonDriver::begin() { pinMode(pin_, INPUT_PULLUP); }

void ButtonDriver::poll(uint32_t now_ms) {
  if (!gate_.due(now_ms, 20)) return;
  bool raw = digitalRead(pin_) == LOW;
  if (raw == last_) stable_ = raw;
  last_ = raw;
  fl::publishBool("button", stable_);
}
