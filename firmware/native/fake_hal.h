#pragma once
// Native fake HAL: implements the fl:: API from FirmLoop.h over the generated channel table.
// Used by harness_main.cpp (and unit_check.cpp). No heap; all storage is static.
#include <stdint.h>

namespace fakehal {

constexpr int MAX_ERRORS = 8;
constexpr int ERROR_LEN = 64;

void reset();                              // neutral values, empty display, no errors, clock 0
int channelIndex(const char* id);          // -1 if unknown
// Harness-side SET of a sensor: parses text per channel type. Records an error and returns false
// on unknown channel, non-sensor channel, or unparsable value.
bool setSensor(const char* id, const char* text);

float floatValue(int idx);
int32_t intValue(int idx);
bool boolValue(int idx);
const char* displayRow(int row);           // NUL-terminated, at most 21 chars

int errorCount();
const char* errorText(int i);
void clearErrors();

void setNow(uint32_t ms);

// State accessor for fl::writeState() (fl_value.h): the harness snapshot and the parity test use
// it so the `s`/`a`/`d` fragment is produced by the same code as the ESP32 telemetry.
struct View {
  float f(int i) const { return floatValue(i); }
  int32_t iv(int i) const { return intValue(i); }
  bool b(int i) const { return boolValue(i); }
  bool eff(int i) const { return boolValue(i); }
  const char* row(int r) const { return displayRow(r); }
};

}  // namespace fakehal
