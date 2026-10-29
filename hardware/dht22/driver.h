#pragma once
#include <DHT.h>

#include "Driver.h"

// DHT22 on one data pin. Channels: temp (C), humidity (%). Sampled every 2000 ms (the part
// cannot be read faster) and not before ~1.5 s after boot, when the part is still settling; a failed or out-of-range read keeps the previous value and reports
// DRIVER_ERROR.
class Dht22Driver : public fl::Driver {
 public:
  explicit Dht22Driver(uint8_t data) : dht_(data, DHT22) {}
  void begin() override;
  void poll(uint32_t now_ms) override;

 private:
  DHT dht_;
  bool armed_ = false;
  uint32_t firstMs_ = 0;
  fl::IntervalGate gate_;
};
