#include "FirmLoop.h"

static volatile uint32_t spin = 0;

void user_setup() {}

void user_tick(uint32_t now_ms) {
  (void)now_ms;
  for (;;) spin = spin + 1;
}
