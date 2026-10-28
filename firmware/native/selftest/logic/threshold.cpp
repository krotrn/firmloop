#include "FirmLoop.h"

void user_setup() { fl::setBool("fan", false); }

void user_tick(uint32_t now_ms) {
  (void)now_ms;
  fl::setBool("fan", fl::readFloat("temp") > 30.0f);
}
