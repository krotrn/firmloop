#include "driver.h"

#include <math.h>

void Dht22Driver::begin() { dht_.begin(); }

void Dht22Driver::poll(uint32_t now_ms) {
  if (!armed_) { armed_ = true; firstMs_ = now_ms; }
  if ((uint32_t)(now_ms - firstMs_) < 1500) return;  // DHT22 needs ~1-2 s after power-up
  if (!gate_.due(now_ms, 2000)) return;
  float t = dht_.readTemperature();
  float h = dht_.readHumidity();
  if (isnan(t) || isnan(h)) {
    fl::reportError("DRIVER_ERROR", "dht22: read failed");
    return;
  }
  if (t < -40.0f || t > 80.0f || h < 0.0f || h > 100.0f) {
    fl::reportError("DRIVER_ERROR", "dht22: reading out of range");
    return;
  }
  fl::publishFloat("temp", t);
  fl::publishFloat("humidity", h);
}
