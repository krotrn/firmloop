// Native unit check for fl_core.h (ESP32 runtime logic: line guard, command parsing/handling,
// test-mode TTL, overrides, error rate limit, RTC block, telemetry cadence/format).
// Built and run by run.sh. Exit code 0 = all good.
#include <stdio.h>
#include <string.h>

#include "fake_hal.h"
#include "fl_core.h"

static int g_fail = 0;
#define CHECK(cond)                                                    \
  do {                                                                 \
    if (!(cond)) {                                                     \
      printf("  FAIL line %d: %s\n", __LINE__, #cond);                 \
      g_fail++;                                                        \
    }                                                                  \
  } while (0)
#define CHECK_STR(a, b)                                                \
  do {                                                                 \
    if (strcmp((a), (b)) != 0) {                                       \
      printf("  FAIL line %d: \"%s\" != \"%s\"\n", __LINE__, (a), (b)); \
      g_fail++;                                                        \
    }                                                                  \
  } while (0)

using namespace fl;

static const char* TOK = "0123456789abcdef";

// Feed a whole string through an assembler; returns the last non-None result.
static LineAssembler::Result feedAll(LineAssembler& a, const char* s, size_t n) {
  LineAssembler::Result r = LineAssembler::None;
  for (size_t i = 0; i < n; ++i) {
    LineAssembler::Result x = a.feed(s[i]);
    if (x != LineAssembler::None) r = x;
  }
  return r;
}

static void test_line_guard() {
  LineAssembler a;
  CHECK(feedAll(a, "{\"id\":1}\n", 9) == LineAssembler::Line);
  CHECK_STR(a.line(), "{\"id\":1}");
  CHECK(feedAll(a, "\n\r\n", 3) == LineAssembler::None);  // empty lines ignored
  CHECK(feedAll(a, "ab\r\n", 4) == LineAssembler::Line);  // CR stripped
  CHECK_STR(a.line(), "ab");

  char big[LINE_CAP + 8];
  memset(big, 'x', sizeof big);
  // exactly LINE_CAP bytes is accepted
  big[LINE_CAP] = '\n';
  CHECK(feedAll(a, big, LINE_CAP + 1) == LineAssembler::Line);
  CHECK(strlen(a.line()) == LINE_CAP);
  // LINE_CAP + 1 is dropped, reported once at the newline, and the next line is fine
  memset(big, 'x', sizeof big);
  big[LINE_CAP + 1] = '\n';
  int tooLong = 0;
  for (size_t i = 0; i < LINE_CAP + 2; ++i)
    if (a.feed(big[i]) == LineAssembler::TooLong) tooLong++;
  CHECK(tooLong == 1);
  CHECK(feedAll(a, "ok\n", 3) == LineAssembler::Line);
  CHECK_STR(a.line(), "ok");
  // very long garbage with no newline never overflows memory and recovers
  for (int i = 0; i < 5000; ++i) a.feed('y');
  CHECK(feedAll(a, "\n", 1) == LineAssembler::TooLong);
  CHECK(feedAll(a, "z\n", 2) == LineAssembler::Line);
}

static void test_parse() {
  Command c;
  CHECK(parseCommand("{\"id\":2,\"cmd\":\"hello\"}", &c) == nullptr);
  CHECK(c.hasId && c.id == 2 && c.cmd == Cmd::Hello);

  CHECK(parseCommand("{\"id\":3,\"cmd\":\"test_start\",\"token\":\"0123456789abcdef\","
                     "\"ttl_ms\":30000,\"given\":{\"temp\":25.5,\"motion\":true,\"light\":1800}}",
                     &c) == nullptr);
  CHECK(c.cmd == Cmd::TestStart && c.ttl_ms == 30000 && c.ngiven == 3);
  CHECK_STR(c.given[0].ch, "temp");
  CHECK(c.given[0].v == 25.5f);
  CHECK(c.given[1].v == 1.0f);
  CHECK(c.given[2].v == 1800.0f);

  // 13 given entries: rejected, id still captured so the caller can ack
  char line[LINE_CAP + 1];
  strcpy(line, "{\"id\":9,\"cmd\":\"test_start\",\"token\":\"0123456789abcdef\",\"ttl_ms\":1,\"given\":{");
  for (int i = 0; i < 13; ++i) {
    char e[16];
    snprintf(e, sizeof e, "%s\"c%d\":1", i ? "," : "", i);
    strcat(line, e);
  }
  strcat(line, "}}");
  CHECK_STR(parseCommand(line, &c), "BAD_COMMAND");
  CHECK(c.hasId && c.id == 9);
  // 12 entries are fine
  strcpy(line, "{\"id\":9,\"cmd\":\"test_start\",\"token\":\"0123456789abcdef\",\"ttl_ms\":1,\"given\":{");
  for (int i = 0; i < 12; ++i) {
    char e[16];
    snprintf(e, sizeof e, "%s\"c%d\":1", i ? "," : "", i);
    strcat(line, e);
  }
  strcat(line, "}}");
  CHECK(parseCommand(line, &c) == nullptr && c.ngiven == 12);

  CHECK_STR(parseCommand("not json", &c), "BAD_COMMAND");
  CHECK_STR(parseCommand("{\"id\":4,\"cmd\":\"nope\"}", &c), "BAD_COMMAND");
  CHECK(c.hasId && c.id == 4);
  CHECK_STR(parseCommand("{\"cmd\":\"hello\",\"id\":1,\"ttl_ms\":0}", &c), "BAD_COMMAND");
  CHECK_STR(parseCommand("{\"id\":1,\"cmd\":\"renew\",\"ttl_ms\":99999999}", &c), "BAD_COMMAND");
  CHECK(parseCommand("{\"cmd\":\"hello\"}", &c) == nullptr && !c.hasId);
  // inject value forms
  CHECK(parseCommand("{\"id\":5,\"cmd\":\"inject\",\"token\":\"t\",\"ch\":\"motion\",\"v\":true}", &c) == nullptr);
  CHECK(c.hasV && c.v == 1.0f);
  CHECK(parseCommand("{\"id\":5,\"cmd\":\"inject\",\"token\":\"t\",\"ch\":\"temp\",\"v\":-3.5}", &c) == nullptr);
  CHECK(c.v == -3.5f);
}

static Command mk(const char* json) {
  Command c;
  const char* e = parseCommand(json, &c);
  if (e) { printf("  parse failed (%s): %s\n", e, json); g_fail++; }
  return c;
}

static void startTest(Core& core, uint32_t now, uint32_t ttl = 30000) {
  GivenEntry g[1];
  strcpy(g[0].ch, "temp");
  g[0].v = 31.0f;
  core.enterTest(TOK, ttl, g, 1, now);
}

static void test_test_mode() {
  Core core;
  core.init();
  // outside test mode: inject / renew / test_off are NOT_IN_TEST_MODE
  Outcome o = core.handle(mk("{\"id\":1,\"cmd\":\"inject\",\"token\":\"0123456789abcdef\",\"ch\":\"temp\",\"v\":1}"), 0);
  CHECK(!o.ok && strcmp(o.err, "NOT_IN_TEST_MODE") == 0);
  o = core.handle(mk("{\"id\":1,\"cmd\":\"test_off\",\"token\":\"0123456789abcdef\"}"), 0);
  CHECK(!o.ok && strcmp(o.err, "NOT_IN_TEST_MODE") == 0 && o.action == Action::None);

  // test_start validates but does not itself change state; the shell restarts
  o = core.handle(mk("{\"id\":2,\"cmd\":\"test_start\",\"token\":\"0123456789abcdef\",\"ttl_ms\":30000,\"given\":{\"temp\":25}}"), 0);
  CHECK(o.ok && o.action == Action::StartTest && !core.testMode());
  o = core.handle(mk("{\"id\":2,\"cmd\":\"test_start\",\"token\":\"short\",\"ttl_ms\":30000}"), 0);
  CHECK(!o.ok && strcmp(o.err, "BAD_COMMAND") == 0);
  o = core.handle(mk("{\"id\":2,\"cmd\":\"test_start\",\"token\":\"0123456789abcdef\",\"given\":{\"temp\":1}}"), 0);
  CHECK(!o.ok && strcmp(o.err, "BAD_COMMAND") == 0);  // ttl required
  o = core.handle(mk("{\"id\":2,\"cmd\":\"test_start\",\"token\":\"0123456789abcdef\",\"ttl_ms\":5,\"given\":{\"zzz\":1}}"), 0);
  CHECK(!o.ok && strcmp(o.err, "UNKNOWN_CHANNEL") == 0);
  o = core.handle(mk("{\"id\":2,\"cmd\":\"test_start\",\"token\":\"0123456789abcdef\",\"ttl_ms\":5,\"given\":{\"fan\":1}}"), 0);
  CHECK(!o.ok && strcmp(o.err, "WRONG_TYPE") == 0);

  // boot into test mode: given pre-filled, neutrals elsewhere
  core.init();
  startTest(core, 1000);
  CHECK(core.testMode());
  CHECK(core.readFloat("temp") == 31.0f);
  CHECK(core.readFloat("humidity") == 50.0f);

  // inject: float, int, bool; token and channel checks
  char inj[160];
  snprintf(inj, sizeof inj, "{\"id\":3,\"cmd\":\"inject\",\"token\":\"%s\",\"ch\":\"temp\",\"v\":32.5}", TOK);
  CHECK(core.handle(mk(inj), 1100).ok);
  CHECK(core.readFloat("temp") == 32.5f);
  snprintf(inj, sizeof inj, "{\"id\":3,\"cmd\":\"inject\",\"token\":\"%s\",\"ch\":\"light\",\"v\":1234}", TOK);
  CHECK(core.handle(mk(inj), 1100).ok);
  CHECK(core.readInt("light") == 1234);
  snprintf(inj, sizeof inj, "{\"id\":3,\"cmd\":\"inject\",\"token\":\"%s\",\"ch\":\"motion\",\"v\":1}", TOK);
  CHECK(core.handle(mk(inj), 1100).ok);
  CHECK(core.readBool("motion"));
  o = core.handle(mk("{\"id\":3,\"cmd\":\"inject\",\"token\":\"ffffffffffffffff\",\"ch\":\"temp\",\"v\":1}"), 1100);
  CHECK(!o.ok && strcmp(o.err, "BAD_TOKEN") == 0);
  CHECK(core.readFloat("temp") == 32.5f);
  snprintf(inj, sizeof inj, "{\"id\":3,\"cmd\":\"inject\",\"token\":\"%s\",\"ch\":\"fan\",\"v\":1}", TOK);
  o = core.handle(mk(inj), 1100);
  CHECK(!o.ok && strcmp(o.err, "WRONG_TYPE") == 0);
  snprintf(inj, sizeof inj, "{\"id\":3,\"cmd\":\"inject\",\"token\":\"%s\",\"ch\":\"nope\",\"v\":1}", TOK);
  o = core.handle(mk(inj), 1100);
  CHECK(!o.ok && strcmp(o.err, "UNKNOWN_CHANNEL") == 0);
  snprintf(inj, sizeof inj, "{\"id\":3,\"cmd\":\"inject\",\"token\":\"%s\",\"ch\":\"temp\"}", TOK);
  o = core.handle(mk(inj), 1100);
  CHECK(!o.ok && strcmp(o.err, "BAD_COMMAND") == 0);

  // TTL: deadline 1000+30000; expires exactly at the deadline
  CHECK(!core.ttlExpired(30999));
  CHECK(core.ttlExpired(31000));
  // renew extends from now
  char ren[160];
  snprintf(ren, sizeof ren, "{\"id\":4,\"cmd\":\"renew\",\"token\":\"%s\",\"ttl_ms\":30000}", TOK);
  CHECK(core.handle(mk(ren), 20000).ok);
  CHECK(!core.ttlExpired(49999));
  CHECK(core.ttlExpired(50000));
  o = core.handle(mk("{\"id\":4,\"cmd\":\"renew\",\"token\":\"ffffffffffffffff\",\"ttl_ms\":30000}"), 20000);
  CHECK(!o.ok && strcmp(o.err, "BAD_TOKEN") == 0);

  // overrides refused in test mode
  o = core.handle(mk("{\"id\":5,\"cmd\":\"override\",\"ch\":\"fan\",\"v\":1,\"ms\":1000}"), 20000);
  CHECK(!o.ok && strcmp(o.err, "NOT_IN_RUN_MODE") == 0);

  // test_off: token check then restart action
  o = core.handle(mk("{\"id\":6,\"cmd\":\"test_off\",\"token\":\"ffffffffffffffff\"}"), 20000);
  CHECK(!o.ok && strcmp(o.err, "BAD_TOKEN") == 0 && o.action == Action::None);
  snprintf(ren, sizeof ren, "{\"id\":6,\"cmd\":\"test_off\",\"token\":\"%s\"}", TOK);
  o = core.handle(mk(ren), 20000);
  CHECK(o.ok && o.action == Action::RestartNormal);

  // TTL wrap-safety near uint32 rollover
  Core w;
  w.init();
  startTest(w, 0xFFFFFF00u, 1000);
  CHECK(!w.ttlExpired(0xFFFFFF10u));
  CHECK(!w.ttlExpired(0x000002E7u));
  CHECK(w.ttlExpired(0x000002E8u));

  // reboot / hello actions
  o = core.handle(mk("{\"id\":7,\"cmd\":\"reboot\"}"), 0);
  CHECK(o.ok && o.action == Action::RestartNormal);
  o = core.handle(mk("{\"id\":7,\"cmd\":\"hello\"}"), 0);
  CHECK(o.ok && o.action == Action::SendHello);
}

static void test_overrides() {
  Core core;
  core.init();
  core.setBool("fan", true);
  Outcome o = core.handle(mk("{\"id\":1,\"cmd\":\"override\",\"ch\":\"fan\",\"v\":0,\"ms\":60000}"), 1000);
  CHECK(o.ok);
  CHECK(!core.actuatorOn("fan"));          // override wins over the user's true
  core.setBool("fan", true);               // user writes ignored meanwhile
  CHECK(!core.actuatorOn("fan"));
  CHECK(core.anyOverride());
  char buf[512];
  JsonWriter tw(buf, sizeof buf);
  core.writeTelemetry(tw, 0);
  CHECK(strstr(buf, "\"mode\":\"override\"") != nullptr);
  CHECK(strstr(buf, "\"fan\":0") != nullptr);

  CHECK(!core.expireOverrides(60999));
  CHECK(core.expireOverrides(61000));      // expiry restores the user's value
  CHECK(core.actuatorOn("fan"));
  CHECK(!core.anyOverride());

  // clear
  core.handle(mk("{\"id\":2,\"cmd\":\"override\",\"ch\":\"led\",\"v\":1,\"ms\":5000}"), 0);
  CHECK(core.actuatorOn("led"));
  CHECK(core.handle(mk("{\"id\":3,\"cmd\":\"override_clear\",\"ch\":\"led\"}"), 0).ok);
  CHECK(!core.actuatorOn("led"));

  // bad targets
  o = core.handle(mk("{\"id\":4,\"cmd\":\"override\",\"ch\":\"temp\",\"v\":1,\"ms\":5}"), 0);
  CHECK(!o.ok && strcmp(o.err, "WRONG_TYPE") == 0);
  o = core.handle(mk("{\"id\":4,\"cmd\":\"override\",\"ch\":\"zzz\",\"v\":1,\"ms\":5}"), 0);
  CHECK(!o.ok && strcmp(o.err, "UNKNOWN_CHANNEL") == 0);
  o = core.handle(mk("{\"id\":4,\"cmd\":\"override\",\"ch\":\"fan\",\"v\":1}"), 0);
  CHECK(!o.ok && strcmp(o.err, "BAD_COMMAND") == 0);
  o = core.handle(mk("{\"id\":4,\"cmd\":\"override_clear\",\"ch\":\"temp\"}"), 0);
  CHECK(!o.ok && strcmp(o.err, "WRONG_TYPE") == 0);
}

static void test_errors_and_semantics() {
  Core core;
  core.init();
  core.setNow(100);
  // same semantics as the native fake HAL
  CHECK(core.readFloat("tmp") == 0.0f);
  CHECK(core.readInt("temp") == 25);
  CHECK(core.readBool("light") == true);
  CHECK(core.readFloat("fan") == 0.0f);
  core.setBool("temp", true);
  Core::PendingError e;
  CHECK(core.popError(&e));
  CHECK_STR(e.code, "UNKNOWN_CHANNEL");
  CHECK_STR(e.msg, "readFloat(\"tmp\")");
  CHECK(core.popError(&e));
  CHECK_STR(e.code, "WRONG_TYPE");
  CHECK_STR(e.msg, "readInt(\"temp\")");
  CHECK(!core.popError(&e));   // the rest are rate-limited (1/s per code)

  // after 1 s a new one is emitted
  core.setNow(1099);
  core.readFloat("tmp");
  CHECK(!core.popError(&e));
  core.setNow(1100);
  core.readFloat("tmp");
  CHECK(core.popError(&e));

  // BAD_ROW, display truncation, clear
  core.setNow(5000);
  core.displayLine(4, "x");
  CHECK(core.popError(&e));
  CHECK_STR(e.code, "BAD_ROW");
  core.displayLine(1, "0123456789012345678901234");
  CHECK_STR(core.displayRow(1), "012345678901234567890");
  core.displayClear();
  CHECK_STR(core.displayRow(1), "");

  // driver publishing
  core.publish("temp", 28.4f, Type::Float);
  CHECK(core.readFloat("temp") == 28.4f);
  core.publish("light", 1500, Type::Int);
  CHECK(core.readInt("light") == 1500);
  core.publish("motion", 1, Type::Bool);
  CHECK(core.readBool("motion"));
}

static void test_telemetry() {
  Core core;
  core.init();
  char buf[512];
  JsonWriter w(buf, sizeof buf);
  core.markSetup(1000);
  core.writeTelemetry(w, 0);
  CHECK(!w.overflow());
  CHECK_STR(buf,
            "{\"type\":\"tel\",\"t\":0,\"mode\":\"run\",\"s\":{\"button\":0,\"humidity\":50.0,"
            "\"light\":2000,\"motion\":0,\"temp\":25.0},\"a\":{\"buzzer\":0,\"fan\":0,\"led\":0},"
            "\"d\":[\"\",\"\",\"\",\"\"]}");
  CHECK(core.rel(1450) == 450);

  // run-mode cadence: not due right after, due after 500 ms or on change
  CHECK(!core.telemetryDue(100));
  CHECK(!core.telemetryDue(499));
  CHECK(core.telemetryDue(500));
  core.setBool("fan", true);
  CHECK(core.telemetryDue(100));
  w.reset(); core.writeTelemetry(w, 100);
  CHECK(!core.telemetryDue(150));
  core.displayLine(0, "Temp: 31.2C");
  CHECK(core.telemetryDue(150));
  w.reset(); core.writeTelemetry(w, 150);
  CHECK(strstr(buf, "\"d\":[\"Temp: 31.2C\",\"\",\"\",\"\"]") != nullptr);
  CHECK(!core.telemetryDue(200));
  // an override change also triggers
  core.handle(mk("{\"id\":1,\"cmd\":\"override\",\"ch\":\"fan\",\"v\":0,\"ms\":1000}"), 200);
  CHECK(core.telemetryDue(200));

  // test mode: always due, mode "test"
  Core t;
  t.init();
  startTest(t, 0);
  CHECK(t.telemetryDue(0));
  w.reset(); t.writeTelemetry(w, 0);
  CHECK(t.telemetryDue(50));
  CHECK(strstr(buf, "\"mode\":\"test\"") != nullptr);
  CHECK(strstr(buf, "\"temp\":31.0") != nullptr);

  // hello / ack / err
  w.reset(); t.writeHello(w);
  CHECK(strstr(buf, "\"type\":\"hello\",\"proto\":1,\"lib\":\"" FL_LIB_VERSION "\",\"fw\":\"native\",\"test\":true") != nullptr);
  CHECK(strstr(buf, "\"channels\":[\"button\",\"buzzer\",\"display\"") != nullptr);
  Outcome ok;
  w.reset(); t.writeAck(w, 7, ok);
  CHECK_STR(buf, "{\"type\":\"ack\",\"id\":7,\"ok\":true}");
  Outcome bad;
  bad.ok = false; bad.err = "BAD_TOKEN";
  w.reset(); t.writeAck(w, 8, bad);
  CHECK_STR(buf, "{\"type\":\"ack\",\"id\":8,\"ok\":false,\"err\":\"BAD_TOKEN\"}");
  w.reset(); t.writeErr(w, 1460, "UNKNOWN_CHANNEL", "readFloat(\"tmp\")");
  CHECK_STR(buf, "{\"type\":\"err\",\"t\":1460,\"code\":\"UNKNOWN_CHANNEL\",\"msg\":\"readFloat(\\\"tmp\\\")\"}");
}

static void test_rtc() {
  Command c = mk("{\"id\":3,\"cmd\":\"test_start\",\"token\":\"0123456789abcdef\","
                 "\"ttl_ms\":30000,\"given\":{\"temp\":25.5,\"motion\":true}}");
  RtcBlock b;
  memset(&b, 0xA5, sizeof b);              // power-up garbage
  CHECK(!rtcValid(b));
  rtcFill(&b, c);
  CHECK(rtcValid(b));
  CHECK_STR(b.token, "0123456789abcdef");
  CHECK(b.ngiven == 2 && b.ttl_ms == 30000);
  // boot path: block -> Core
  Core core;
  core.init();
  core.enterTest(b.token, b.ttl_ms, b.given, b.ngiven, 100);
  CHECK(core.testMode() && core.readFloat("temp") == 25.5f && core.readBool("motion"));
  // corruption detected
  RtcBlock bad = b;
  bad.given[0].v = 99.0f;
  CHECK(!rtcValid(bad));
  rtcClear(&b);
  CHECK(!rtcValid(b));
}

static void test_bad_numbers() {
  Command c;
  // non-finite / absurd values are BAD_COMMAND at parse time (never reach the table)
  CHECK_STR(parseCommand("{\"id\":1,\"cmd\":\"inject\",\"token\":\"t\",\"ch\":\"temp\",\"v\":nan}", &c), "BAD_COMMAND");
  CHECK(c.hasId && c.id == 1);
  CHECK_STR(parseCommand("{\"id\":1,\"cmd\":\"inject\",\"token\":\"t\",\"ch\":\"temp\",\"v\":inf}", &c), "BAD_COMMAND");
  CHECK_STR(parseCommand("{\"id\":1,\"cmd\":\"inject\",\"token\":\"t\",\"ch\":\"temp\",\"v\":-Infinity}", &c), "BAD_COMMAND");
  CHECK_STR(parseCommand("{\"id\":1,\"cmd\":\"inject\",\"token\":\"t\",\"ch\":\"temp\",\"v\":1e30}", &c), "BAD_COMMAND");
  CHECK_STR(parseCommand("{\"id\":1,\"cmd\":\"inject\",\"token\":\"t\",\"ch\":\"light\",\"v\":99999999999}", &c), "BAD_COMMAND");
  CHECK_STR(parseCommand("{\"id\":1,\"cmd\":\"inject\",\"token\":\"t\",\"ch\":\"temp\",\"v\":\"x\"}", &c), "BAD_COMMAND");
  CHECK(parseCommand("{\"id\":1,\"cmd\":\"inject\",\"token\":\"t\",\"ch\":\"light\",\"v\":999999999}", &c) == nullptr);
  CHECK_STR(parseCommand("{\"id\":1,\"cmd\":\"test_start\",\"token\":\"0123456789abcdef\",\"ttl_ms\":5,\"given\":{\"temp\":nan}}", &c), "BAD_COMMAND");
  CHECK_STR(parseCommand("{\"id\":1,\"cmd\":\"test_start\",\"token\":\"0123456789abcdef\",\"ttl_ms\":5,\"given\":{\"temp\":1e999}}", &c), "BAD_COMMAND");
  CHECK_STR(parseCommand("{\"id\":1,\"cmd\":\"override\",\"ch\":\"fan\",\"v\":nan,\"ms\":5}", &c), "BAD_COMMAND");

  // Core::setSensor itself refuses (defence in depth for drivers / direct callers)
  Core core;
  core.init();
  int t = core.indexOf("temp"), l = core.indexOf("light");
  CHECK(!core.setSensor(t, NAN));
  CHECK(!core.setSensor(t, INFINITY));
  CHECK(!core.setSensor(l, 3e9f));
  CHECK(core.readFloat("temp") == 25.0f && core.readInt("light") == 2000);
  CHECK(core.setSensor(l, 1e9f) && core.readInt("light") == 1000000000);
  core.publish("temp", NAN, Type::Float);
  CHECK(core.readFloat("temp") == 25.0f);
  Core::PendingError e;
  CHECK(core.popError(&e));
  CHECK_STR(e.code, "DRIVER_ERROR");

  // a command struct from the parser has a zeroed `given`
  Command z;
  bool allZero = true;
  for (uint8_t i = 0; i < MAX_GIVEN; ++i) allZero = allZero && z.given[i].ch[0] == 0 && z.given[i].v == 0;
  CHECK(allZero);
}

// Core and the native fake HAL must agree on every channel operation, and print byte-identical
// `s`/`a`/`d` fragments.
static void test_parity() {
  Core core;
  core.init();
  core.setNow(0);
  fakehal::reset();

  // sensor inputs: valid and rejected
  struct In { const char* ch; const char* text; };
  const In ins[] = {{"temp", "31.57"}, {"humidity", "49.95"}, {"light", "1234"}, {"motion", "1"},
                    {"button", "true"}, {"temp", "nan"}, {"humidity", "inf"}, {"light", "99999999999"},
                    {"light", "2e9"}, {"temp", "-40"}, {"fan", "1"}, {"zzz", "1"}};
  for (const In& in : ins) {
    int idx = core.indexOf(in.ch);
    bool a = false;
    if (idx >= 0 && CHANNELS[idx].kind == Kind::Sensor) {
      float x;
      if (strcmp(in.text, "true") == 0) x = 1;
      else x = strtof(in.text, nullptr);
      a = core.setSensor(idx, x);
    }
    bool b = fakehal::setSensor(in.ch, in.text);
    if (a != b) { printf("  parity: set %s=%s core=%d fake=%d\n", in.ch, in.text, a, b); g_fail++; }
  }
  fakehal::clearErrors();

  // reads, including wrong type (neutral) and unknown id (0)
  const char* chans[] = {"temp", "humidity", "light", "motion", "button", "fan", "led", "buzzer",
                         "display", "zzz"};
  for (const char* ch : chans) {
    float cf = core.readFloat(ch), ff = fl::readFloat(ch);
    int32_t ci = core.readInt(ch), fi = fl::readInt(ch);
    bool cb = core.readBool(ch), fb = fl::readBool(ch);
    if (cf != ff || ci != fi || cb != fb) {
      printf("  parity: read %s core=(%g,%d,%d) fake=(%g,%d,%d)\n", ch, cf, (int)ci, cb, ff, (int)fi, fb);
      g_fail++;
    }
  }
  // writes: valid, wrong type, unknown
  const char* outs[] = {"fan", "led", "temp", "display", "zzz", "buzzer"};
  for (int k = 0; k < 6; ++k) { core.setBool(outs[k], k % 2 == 0); fl::setBool(outs[k], k % 2 == 0); }
  // display: truncation, bad row, clear then rewrite
  core.displayLine(0, "Temp: 31.6C");
  fl::displayLine(0, "Temp: 31.6C");
  core.displayLine(2, "0123456789012345678901234");
  fl::displayLine(2, "0123456789012345678901234");
  core.displayLine(9, "x");
  fl::displayLine(9, "x");
  core.displayLine(3, "tab\t\"q\"");
  fl::displayLine(3, "tab\t\"q\"");
  core.displayLine(1, "gone");
  fl::displayLine(1, "gone");
  core.displayClear();
  fl::displayClear();
  core.displayLine(1, "back");
  fl::displayLine(1, "back");

  char cbuf[512], fbuf[512];
  JsonWriter cw(cbuf, sizeof cbuf), fw(fbuf, sizeof fbuf);
  core.writeTelemetry(cw, 0);
  fw.beginObj();
  writeState(fw, fakehal::View());
  fw.endObj();
  // Core: {"type":"tel","t":0,"mode":"run",<fragment>}   fake: {<fragment>}
  const char* cf = strstr(cbuf, "\"s\":");
  CHECK(cf != nullptr);
  size_t clen = strlen(cf) - 1;  // drop closing '}'
  const char* ff = fbuf + 1;
  size_t flen = strlen(ff) - 1;
  CHECK(cf && clen == flen && strncmp(cf, ff, flen) == 0);
  if (!(cf && clen == flen && strncmp(cf, ff, flen) == 0)) printf("  core: %s\n  fake: %s\n", cbuf, fbuf);
  CHECK(strstr(cbuf, "nan") == nullptr && strstr(fbuf, "nan") == nullptr);
}

int main() {
  test_line_guard();
  test_parse();
  test_test_mode();
  test_overrides();
  test_errors_and_semantics();
  test_telemetry();
  test_rtc();
  test_bad_numbers();
  test_parity();
  if (g_fail) { printf("core_check: %d failure(s)\n", g_fail); return 1; }
  printf("core_check: ok\n");
  return 0;
}
