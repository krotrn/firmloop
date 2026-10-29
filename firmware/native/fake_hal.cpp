#include "fake_hal.h"

#include <stdlib.h>
#include <string.h>

#include "FirmLoop.h"
#include "FirmLoopInternal.h"
#include "fl_value.h"

namespace {

struct Val {
  float f;
  int32_t i;
  bool b;
};

Val g_val[fl::MAX_CHANNELS];
char g_rows[fl::DISPLAY_ROWS][fl::DISPLAY_COLS + 1];
char g_err[fakehal::MAX_ERRORS][fakehal::ERROR_LEN];
int g_nerr = 0;
uint32_t g_now = 0;

void addError(const char* code, const char* fn, const char* arg) {
  char t[fakehal::ERROR_LEN];
  snprintf(t, sizeof t, "%s: %s(\"%s\")", code, fn, arg);
  for (int k = 0; k < g_nerr; ++k)
    if (strcmp(g_err[k], t) == 0) return;  // de-duplicate within a tick
  if (g_nerr < fakehal::MAX_ERRORS) {
    strcpy(g_err[g_nerr], t);
    g_nerr++;
  }
}

// Returns the channel index, or -1 for an unknown channel (UNKNOWN_CHANNEL recorded).
// A type/kind mismatch on an existing channel records WRONG_TYPE, sets *mismatch and still
// returns the index so the caller can return the channel's neutral value (5.4).
int lookup(const char* ch, fl::Type type, fl::Kind kind, const char* fn, bool* mismatch) {
  *mismatch = false;
  int i = fakehal::channelIndex(ch);
  if (i < 0) {
    addError("UNKNOWN_CHANNEL", fn, ch ? ch : "");
    return -1;
  }
  if (fl::CHANNELS[i].type != type || fl::CHANNELS[i].kind != kind) {
    addError("WRONG_TYPE", fn, ch);
    *mismatch = true;
  }
  return i;
}

}  // namespace

namespace fakehal {

void reset() {
  for (int i = 0; i < fl::CHANNEL_COUNT && i < fl::MAX_CHANNELS; ++i) {
    float n = fl::CHANNELS[i].neutral;
    g_val[i].f = n;
    g_val[i].i = (int32_t)n;
    g_val[i].b = n != 0.0f;
  }
  for (int r = 0; r < fl::DISPLAY_ROWS; ++r) g_rows[r][0] = 0;
  g_nerr = 0;
  g_now = 0;
}

int channelIndex(const char* id) {
  if (!id) return -1;
  for (int i = 0; i < fl::CHANNEL_COUNT; ++i)
    if (strcmp(fl::CHANNELS[i].id, id) == 0) return i;
  return -1;
}

bool setSensor(const char* id, const char* text) {
  int i = channelIndex(id);
  if (i < 0) {
    addError("UNKNOWN_CHANNEL", "SET", id ? id : "");
    return false;
  }
  const fl::ChannelDef& d = fl::CHANNELS[i];
  if (d.kind != fl::Kind::Sensor) {
    addError("WRONG_TYPE", "SET", id);
    return false;
  }
  char* end = nullptr;
  if (d.type == fl::Type::Float) {
    float v = strtof(text, &end);
    if (end == text || !fl::validSensorNumber(v)) { addError("BAD_VALUE", "SET", id); return false; }
    g_val[i].f = v;
  } else if (d.type == fl::Type::Int) {
    // Parsed as a float then truncated, exactly like the ESP32 command path (fl_core.h).
    float v = strtof(text, &end);
    if (end == text || !fl::validSensorNumber(v)) {
      addError("BAD_VALUE", "SET", id);
      return false;
    }
    g_val[i].i = (int32_t)v;
  } else if (d.type == fl::Type::Bool) {
    if (strcmp(text, "1") == 0 || strcmp(text, "true") == 0) g_val[i].b = true;
    else if (strcmp(text, "0") == 0 || strcmp(text, "false") == 0) g_val[i].b = false;
    else { addError("BAD_VALUE", "SET", id); return false; }
  } else {
    addError("WRONG_TYPE", "SET", id);
    return false;
  }
  return true;
}

float floatValue(int idx) { return g_val[idx].f; }
int32_t intValue(int idx) { return g_val[idx].i; }
bool boolValue(int idx) { return g_val[idx].b; }
const char* displayRow(int row) { return g_rows[row]; }

int errorCount() { return g_nerr; }
const char* errorText(int i) { return g_err[i]; }
void clearErrors() { g_nerr = 0; }
void setNow(uint32_t ms) { g_now = ms; }

}  // namespace fakehal

namespace fl {

float readFloat(const char* channel) {
  bool bad;
  int i = lookup(channel, Type::Float, Kind::Sensor, "readFloat", &bad);
  if (i < 0) return 0.0f;
  return bad ? CHANNELS[i].neutral : g_val[i].f;
}

int32_t readInt(const char* channel) {
  bool bad;
  int i = lookup(channel, Type::Int, Kind::Sensor, "readInt", &bad);
  if (i < 0) return 0;
  return bad ? (int32_t)CHANNELS[i].neutral : g_val[i].i;
}

bool readBool(const char* channel) {
  bool bad;
  int i = lookup(channel, Type::Bool, Kind::Sensor, "readBool", &bad);
  if (i < 0) return false;
  return bad ? CHANNELS[i].neutral != 0.0f : g_val[i].b;
}

void setBool(const char* channel, bool on) {
  bool bad;
  int i = lookup(channel, Type::Bool, Kind::Actuator, "setBool", &bad);
  if (i >= 0 && !bad) g_val[i].b = on;
}

void displayLine(uint8_t row, const char* text) {
  if (row >= DISPLAY_ROWS) {
    char t[8];
    snprintf(t, sizeof t, "%u", (unsigned)row);
    char e[fakehal::ERROR_LEN];
    snprintf(e, sizeof e, "BAD_ROW: displayLine(%s)", t);
    for (int k = 0; k < g_nerr; ++k)
      if (strcmp(g_err[k], e) == 0) return;
    if (g_nerr < fakehal::MAX_ERRORS) strcpy(g_err[g_nerr++], e);
    return;
  }
  strncpy(g_rows[row], text ? text : "", DISPLAY_COLS);
  g_rows[row][DISPLAY_COLS] = 0;
}

void displayClear() {
  for (int r = 0; r < DISPLAY_ROWS; ++r) g_rows[r][0] = 0;
}

uint32_t now() { return g_now; }

}  // namespace fl
