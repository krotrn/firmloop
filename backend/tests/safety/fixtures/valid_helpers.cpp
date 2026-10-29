#include "FirmLoop.h"

// Beeps for 200 ms after motion; helper, switch, bounded for loop, constants.
static const int32_t BEEP_MS = 200;
static constexpr float LIMIT = 28.5f;
static uint32_t beepUntil = 0;
static uint8_t mode = 0;

static bool tooHot(float t) {
  return t >= LIMIT;
}

static int clampInt(int v, int lo, int hi) {
  if (v < lo) return lo;
  if (v > hi) return hi;
  return v;
}

static void showTemp(float t) {
  char line[22] = "";
  snprintf(line, sizeof(line), "T=%.1f %d", t, tooHot(t) ? 1 : 0);
  fl::displayLine(1, line);
}

void user_setup() {
  fl::displayClear();
  fl::setBool("buzzer", false);
}

void user_tick(uint32_t now_ms) {
  bool motion = fl::readBool("motion");
  int light = clampInt(fl::readInt("light"), 0, 4095);
  float h = fabsf(fl::readFloat("humidity"));
  int hits = 0;
  for (int i = 0; i < 64; i++) {
    if (light > i * 60) hits++;
  }
  for (uint8_t k = 10; k > 0; k--) {
    hits += k;
  }
  switch (mode) {
    case 0:
      if (motion) { mode = 1; beepUntil = now_ms + BEEP_MS; }
      break;
    case 1:
      if (now_ms >= beepUntil) mode = 0;
      break;
    default:
      mode = 0;
      break;
  }
  fl::setBool("buzzer", mode == 1);
  fl::setBool("led", hits > 3 && h < 90.0f);
  showTemp(fl::readFloat("temp"));
  char msg[32] = "";
  snprintf(msg, sizeof msg, "L=%d H=%d", light, hits);
  fl::displayLine(2, msg);
}
