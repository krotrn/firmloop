#include "FirmLoop.h"

void user_setup() {}

void user_tick(uint32_t now_ms) {
  (void)now_ms;
  char line[22];
  snprintf(line, sizeof line, "Temp: %.1fC", fl::readFloat("temp"));
  fl::displayLine(0, line);
}
