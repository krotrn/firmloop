#pragma once
// FirmLoop public API. This header is a contract: changing it requires bumping
// FL_LIB_VERSION (and library.properties).
#include <stdint.h>
#include <stdio.h>     // snprintf only
#include <string.h>    // strcmp, strncpy only
#include <math.h>      // fabsf etc.

#define FL_LIB_VERSION "0.1.0"  // must equal library.properties version= (5.5)
#define FL_PROTO 1              // serial protocol version (5.3)
#define FL_TICK_MS 50           // user_tick period

namespace fl {
  // Sensors (unknown id or wrong type -> runtime error, see 5.4)
  float   readFloat(const char* channel);
  int32_t readInt(const char* channel);
  bool    readBool(const char* channel);

  // Actuators
  void setBool(const char* channel, bool on);

  // Display: row 0..3, text truncated to 21 chars
  void displayLine(uint8_t row, const char* text);
  void displayClear();

  // Time since boot in ms (fake clock in native tests)
  uint32_t now();
}

// Generated code MUST define exactly these two functions:
void user_setup();                 // called once after drivers start
void user_tick(uint32_t now_ms);   // called every FL_TICK_MS (50 ms); must return quickly
