#include "FirmLoop.h"

static bool fanOn = false;

void user_setup() { fl::setBool("fan", false); }

void user_tick(uint32_t now_ms) {
  (void)now_ms;
  float t = fl::readFloat("temp");
  if (!fanOn && t > 30.0f) fanOn = true;
  else if (fanOn && t < 29.5f) fanOn = false;
  fl::setBool("fan", fanOn);
}
