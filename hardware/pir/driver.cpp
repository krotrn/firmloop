#include "driver.h"

#include <Arduino.h>

void PirDriver::begin() { pinMode(pin_, INPUT); }

void PirDriver::poll(uint32_t now_ms) {
  if (!gate_.due(now_ms, 50)) return;
  fl::publishBool("motion", digitalRead(pin_) == HIGH);
}
