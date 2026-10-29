// FirmLoop sketch template. Everything lives in the FirmLoop library;
// user_logic.cpp, channels.generated.cpp and drivers.generated.cpp sit beside this file.
#include <FirmLoop.h>
#include <FirmLoopInternal.h>

void setup() { fl::runtime_begin(); }

void loop() { fl::runtime_service(millis()); }
