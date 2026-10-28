#include "FirmLoop.h"

static volatile size_t g_len = 64;
static char g_src[256];

void user_setup() { memset(g_src, 'A', sizeof g_src); }

// Overruns buf by more than the stack-protector canary; the abort on return must read as
// "killed", not as a clean run.
__attribute__((noinline)) static void smash() {
  char buf[8];
  memcpy(buf, g_src, g_len);
  fl::displayLine(0, buf);
}

void user_tick(uint32_t now_ms) {
  (void)now_ms;
  smash();
}
