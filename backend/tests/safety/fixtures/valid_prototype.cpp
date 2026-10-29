#include "FirmLoop.h"
static float scale(float v);
static float lastTemp = 0.0f;
static float scale(float v) { return v * 2.0f; }
void user_setup() {}
void user_tick(uint32_t t) {
  lastTemp = scale(fl::readFloat("temp"));
  fl::setBool("fan", lastTemp > 60.0f && (t % 1000u) < 500u);
}
