// FirmLoop ESP32 runtime (ARCHITECTURE 5.2-5.4). Arduino-ESP32 only; the native build uses
// firmware/native/fake_hal.cpp instead. All protocol/table logic lives in fl_core.h (unit-tested
// natively); this file only wires it to Serial, the RTC block, ESP.restart() and the drivers.
#ifdef ARDUINO

#include <Arduino.h>

#include "Driver.h"
#include "FirmLoop.h"
#include "FirmLoopInternal.h"
#include "fl_core.h"

namespace {

// Survives a software restart, not a power loss. Never trusted without magic + checksum.
RTC_NOINIT_ATTR fl::RtcBlock g_rtc;

fl::Core g_core;
fl::LineAssembler g_line;
// Worst-case outgoing line, sized from the compile-time channel limit (CHANNEL_COUNT itself is
// defined in generated code): header ~80, per channel key (<= NAME_CAP) + value (<= 14) + quotes
// and separators, 4 display rows of 21 chars each escaped to at most 6 bytes (\u00xx).
constexpr size_t OUT_CAP =
    96 + (size_t)fl::MAX_CHANNELS * (fl::NAME_CAP + 20) +
    (size_t)fl::DISPLAY_ROWS * (fl::DISPLAY_COLS * 6 + 4);
static_assert(OUT_CAP <= 4096, "outgoing line buffer must stay small");
char g_out[OUT_CAP];
uint32_t g_nextTick = 0;  // absolute millis() of the next user_tick
bool g_ready = false;     // user_setup() has run

void emit(fl::JsonWriter& w) {
  if (w.overflow()) {
    // Never send a truncated (invalid) line, and never drop one silently: queue a (rate-limited)
    // DRIVER_ERROR; it is sent on the next tick from a fresh buffer.
    g_core.reportError("DRIVER_ERROR", "output line too long, dropped", nullptr);
    return;
  }
  Serial.write((const uint8_t*)w.c_str(), w.size());
  Serial.write('\n');
}

void sendHello() {
  fl::JsonWriter w(g_out, sizeof g_out);
  g_core.writeHello(w);
  emit(w);
}

void sendErr(const char* code, const char* msg, uint32_t now) {
  fl::JsonWriter w(g_out, sizeof g_out);
  g_core.writeErr(w, g_ready ? g_core.rel(now) : 0, code, msg);
  emit(w);
}

void sendAck(const fl::Command& c, const fl::Outcome& o) {
  fl::JsonWriter w(g_out, sizeof g_out);
  g_core.writeAck(w, c.id, o);
  emit(w);
}

[[noreturn]] void restartNow() {
  Serial.flush();
  delay(20);
  ESP.restart();
  for (;;) {}
}

void handleLine(const char* line, uint32_t now) {
  fl::Command c;
  const char* perr = fl::parseCommand(line, &c);
  if (perr) {
    if (c.hasId) {
      fl::Outcome o;
      o.ok = false;
      o.err = perr;
      sendAck(c, o);
    } else {
      sendErr(perr, "bad command line", now);
    }
    return;
  }
  fl::Outcome o = g_core.handle(c, now);
  if (c.hasId) sendAck(c, o);
  if (!o.ok) return;
  switch (o.action) {
    case fl::Action::SendHello: sendHello(); break;
    case fl::Action::StartTest:
      fl::rtcFill(&g_rtc, c);
      restartNow();
    case fl::Action::RestartNormal:
      fl::rtcClear(&g_rtc);
      restartNow();
    case fl::Action::None: break;
  }
}

void drainErrors(uint32_t now) {
  fl::Core::PendingError e;
  while (g_core.popError(&e)) sendErr(e.code, e.msg, now);
}

void readSerial(uint32_t now) {
  int budget = 512;  // bound work per loop() so ticks stay on time
  while (budget-- > 0 && Serial.available() > 0) {
    int ch = Serial.read();
    if (ch < 0) break;
    fl::LineAssembler::Result r = g_line.feed((char)ch);
    if (r == fl::LineAssembler::Line) handleLine(g_line.line(), now);
    else if (r == fl::LineAssembler::TooLong) sendErr("LINE_TOO_LONG", "line over 256 bytes", now);
  }
}

}  // namespace

namespace fl {

// ---- user API (FirmLoop.h)
float readFloat(const char* channel) { return g_core.readFloat(channel); }
int32_t readInt(const char* channel) { return g_core.readInt(channel); }
bool readBool(const char* channel) { return g_core.readBool(channel); }
void setBool(const char* channel, bool on) { g_core.setBool(channel, on); }
void displayLine(uint8_t row, const char* text) { g_core.displayLine(row, text); }
void displayClear() { g_core.displayClear(); }
uint32_t now() { return g_core.rel(millis()); }  // ms since user_setup(), like native (D-063)

// ---- driver API (Driver.h)
void publishFloat(const char* channel, float v) { g_core.publish(channel, v, Type::Float); }
void publishInt(const char* channel, int32_t v) { g_core.publish(channel, (float)v, Type::Int); }
void publishBool(const char* channel, bool v) { g_core.publish(channel, v ? 1.0f : 0.0f, Type::Bool); }
bool actuatorOn(const char* channel) { return g_core.actuatorOn(channel); }
const char* displayText(uint8_t row) { return g_core.displayRow(row); }
uint32_t displayRevision() { return g_core.displayRevision(); }
void reportError(const char* code, const char* msg) { g_core.reportError(code, msg, nullptr); }

void runtime_begin() {
  Serial.setRxBufferSize(1024);
  Serial.begin(115200);
  g_core.init();
  uint32_t now = millis();
  g_core.setNow(now);

  // Test mode lives in RTC memory only for exactly one boot: consume the block here so a crash
  // or brown-out during a test can never leave the board in test mode (D-006, D-063).
  bool test = fl::rtcValid(g_rtc);
  fl::RtcBlock blk = g_rtc;
  fl::rtcClear(&g_rtc);

  for (uint8_t i = 0; i < DRIVER_COUNT; ++i) {
    DRIVERS[i]->begin();
    DRIVERS[i]->neutral();
  }
  if (test) g_core.enterTest(blk.token, blk.ttl_ms, blk.given, blk.ngiven, now);

  sendHello();
  uint32_t t0 = millis();
  g_core.markSetup(t0);
  g_ready = true;
  user_setup();
  g_nextTick = t0;  // first tick at t=0, like the native harness
  for (uint8_t i = 0; i < DRIVER_COUNT; ++i) DRIVERS[i]->apply(g_core.relNow());
  if (test) {
    fl::JsonWriter w(g_out, sizeof g_out);
    w.beginObj();
    w.key("type"); w.str("test_ready");
    w.key("t"); w.u32(0);
    w.endObj();
    emit(w);
  }
}

void runtime_service(uint32_t now_ms) {
  g_core.setNow(now_ms);

  readSerial(now_ms);

  if (g_core.ttlExpired(now_ms)) {  // test mode never outlives its TTL: restart into normal mode
    fl::rtcClear(&g_rtc);
    restartNow();
  }
  g_core.expireOverrides(now_ms);

  // Sensors: injected values must not be overwritten, so no polling in test mode.
  if (!g_core.testMode())
    for (uint8_t i = 0; i < DRIVER_COUNT; ++i) DRIVERS[i]->poll(now_ms);

  if ((int32_t)(now_ms - g_nextTick) >= 0) {
    uint32_t rel = g_core.rel(g_nextTick);  // scheduled tick time, relative to user_setup()
    user_tick(rel);
    g_nextTick += FL_TICK_MS;
    if ((int32_t)(now_ms - g_nextTick) > 5 * FL_TICK_MS) g_nextTick = now_ms + FL_TICK_MS;  // resync

    for (uint8_t i = 0; i < DRIVER_COUNT; ++i) DRIVERS[i]->apply(rel);
    drainErrors(now_ms);
    if (g_core.telemetryDue(rel)) {
      fl::JsonWriter w(g_out, sizeof g_out);
      g_core.writeTelemetry(w, rel);
      emit(w);
    }
  } else {
    // keep overrides/actuators responsive between ticks (e.g. override expiry or a command)
    drainErrors(now_ms);
  }
}

}  // namespace fl

#endif  // ARDUINO
