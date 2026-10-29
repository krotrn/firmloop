#include "driver.h"

#include <Arduino.h>

void LdrDriver::begin() {
  analogReadResolution(12);
  pinMode(pin_, INPUT);
}

void LdrDriver::poll(uint32_t now_ms) {
  if (!gate_.due(now_ms, 100)) return;
  int32_t sum = 0;
  for (uint8_t i = 0; i < 4; ++i) sum += analogRead(pin_);
  fl::publishInt("light", sum / 4);
}
