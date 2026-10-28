#include "FirmLoop.h"

void user_setup() {}

void user_tick(uint32_t now_ms) {
  (void)now_ms;
  fl::setBool("fan", fl::readFloat("tmp") > 30.0f);  // typo: no such channel
}
