#include "FirmLoop.h"

static bool wasPressed = false;
static bool beeping = false;
static uint32_t beepEnd = 0;

void user_setup() { fl::setBool("buzzer", false); }

void user_tick(uint32_t now_ms) {
  bool pressed = fl::readBool("button");
  if (pressed && !wasPressed) {
    beeping = true;
    beepEnd = now_ms + 200;
  }
  wasPressed = pressed;
  if (beeping && now_ms >= beepEnd) beeping = false;
  fl::setBool("buzzer", beeping);
}
