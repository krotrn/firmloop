#pragma once
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <Wire.h>

#include "Driver.h"

// SSD1306 128x64 OLED on I2C (0x3C). Channel: display (4 rows x 21 columns, 6x8 font, one text
// row per 16 px). Redraws only when the text actually changed (at most once per 50 ms tick; ~25 ms at 400 kHz, estimated). If the panel is missing, begin() reports
// DRIVER_ERROR once and the driver does nothing.
class Ssd1306Driver : public fl::Driver {
 public:
  Ssd1306Driver(uint8_t sda, uint8_t scl) : sda_(sda), scl_(scl), oled_(128, 64, &Wire, -1, 400000UL, 400000UL) {}
  void begin() override;
  void apply(uint32_t now_ms) override;
  void neutral() override;

 private:
  void redraw();
  uint8_t sda_, scl_;
  Adafruit_SSD1306 oled_;
  bool ok_ = false;
  bool drawn_ = false;
  uint32_t rev_ = 0;
  char shown_[4][22];  // copy of the last frame sent to the panel
};
