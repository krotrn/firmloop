#include "FirmLoop.h"

void user_setup() {}

void user_tick(uint32_t now_ms) {
  (void)now_ms;
  FILE* f = fopen("/tmp/firmloop_selftest", "w");  // forbidden once seccomp strict is on
  if (f) fclose(f);
}
