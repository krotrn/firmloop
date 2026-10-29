#include "driver.h"

#include <string.h>

void Ssd1306Driver::begin() {
  Wire.begin(sda_, scl_);
  ok_ = oled_.begin(SSD1306_SWITCHCAPVCC, 0x3C);
  // Fast mode: a full 1 KB frame takes ~25 ms instead of ~100 ms. The library also restores the
  // clock to its "after" value (see constructor) on every display(), so both are 400 kHz.
  Wire.setClock(400000);
  if (!ok_) {
    fl::reportError("DRIVER_ERROR", "ssd1306: display not found at 0x3C");
    return;
  }
  for (uint8_t r = 0; r < 4; ++r) shown_[r][0] = 0;
  oled_.setTextSize(1);
  oled_.setTextColor(SSD1306_WHITE);
  oled_.setTextWrap(false);
  oled_.clearDisplay();
  oled_.display();
}

void Ssd1306Driver::neutral() {
  if (!ok_) return;
  oled_.clearDisplay();
  oled_.display();
}

void Ssd1306Driver::redraw() {
  oled_.clearDisplay();
  for (uint8_t r = 0; r < 4; ++r) {
    oled_.setCursor(0, r * 16);
    oled_.print(fl::displayText(r));
  }
  oled_.display();
}

void Ssd1306Driver::apply(uint32_t now_ms) {
  (void)now_ms;
  if (!ok_) return;
  uint32_t rev = fl::displayRevision();
  if (drawn_ && rev == rev_) return;
  rev_ = rev;
  bool changed = !drawn_;
  for (uint8_t r = 0; r < 4; ++r) {
    if (strcmp(shown_[r], fl::displayText(r)) != 0) {
      strncpy(shown_[r], fl::displayText(r), sizeof shown_[r] - 1);
      shown_[r][sizeof shown_[r] - 1] = 0;
      changed = true;
    }
  }
  drawn_ = true;
  if (changed) redraw();
}
