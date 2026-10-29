#pragma once
// Portable runtime core (ARCHITECTURE 5.2-5.4): channel table, serial command parsing and
// handling, line guard, test-mode TTL, overrides, error rate limit, telemetry/ack/hello writers.
// Pure C++17 with no Arduino dependency so the native self-tests can exercise it; FirmLoop.cpp
// (ESP32 only) wires it to Serial, ESP.restart(), the RTC block and the drivers.
// No heap allocation anywhere; all storage is inside the Core object.
#include <stdint.h>
#include <string.h>

#include "FirmLoop.h"
#include "FirmLoopInternal.h"
#include "fl_json.h"
#include "fl_value.h"

namespace fl {

constexpr size_t LINE_CAP = 256;       // longest accepted line, excluding the newline (5.3)
constexpr uint8_t MAX_GIVEN = 12;      // test_start `given` entries (5.3)
constexpr uint8_t NAME_CAP = 24;       // channel id buffer (incl. NUL)
constexpr uint32_t TELEMETRY_RUN_MS = 500;
constexpr uint32_t ERROR_PERIOD_MS = 1000;   // 1 error per code per second (5.4)
constexpr uint32_t MAX_TTL_MS = 600000;
constexpr uint32_t MAX_OVERRIDE_MS = 3600000;

// ---------------------------------------------------------------- line assembler (line guard)

class LineAssembler {
 public:
  enum Result : uint8_t { None, Line, TooLong };
  // Feed one byte. Line: line() is valid until the next feed(). TooLong: the whole oversized line
  // was dropped (reported once, at its newline). Empty lines are ignored.
  Result feed(char c) {
    if (done_) { done_ = false; len_ = 0; over_ = false; }
    if (c == '\r') return None;
    if (c == '\n') {
      if (over_) { len_ = 0; over_ = false; return TooLong; }
      if (len_ == 0) return None;
      buf_[len_] = 0;
      done_ = true;
      return Line;
    }
    if (over_) return None;
    if (len_ >= LINE_CAP) { over_ = true; return None; }
    buf_[len_++] = c;
    return None;
  }
  const char* line() const { return buf_; }

 private:
  char buf_[LINE_CAP + 1] = {0};
  size_t len_ = 0;
  bool over_ = false;
  bool done_ = false;
};

// ---------------------------------------------------------------- commands

enum class Cmd : uint8_t {
  Hello, TestStart, Inject, Renew, TestOff, Override, OverrideClear, Reboot, Unknown
};

struct GivenEntry {
  char ch[NAME_CAP];
  float v;
};

struct Command {
  int32_t id = 0;
  bool hasId = false;
  Cmd cmd = Cmd::Unknown;
  char token[17] = {0};
  uint32_t ttl_ms = 0;
  char ch[NAME_CAP] = {0};
  float v = 0;
  bool hasV = false;
  uint32_t ms = 0;
  GivenEntry given[MAX_GIVEN] = {};
  uint8_t ngiven = 0;
};

namespace coredetail {
// Number or bool at obj[key]; false if malformed, non-finite or beyond +-MAX_SENSOR_ABS.
inline bool getNum(const char* obj, const char* key, float* out) {
  bool b;
  if (jsonGetBool(obj, key, &b)) { *out = b ? 1.0f : 0.0f; return true; }
  return jsonGetFloat(obj, key, out) && validSensorNumber(*out);
}
// Parse the `given` object into out[]; false if malformed, too many entries or a name too long.
inline bool parseGiven(const char* obj, Command* c) {
  using namespace jsondetail;
  const char* p = skipWs(obj);
  if (*p != '{') return false;
  ++p;
  c->ngiven = 0;
  for (;;) {
    p = skipWs(p);
    if (*p == '}') return true;
    if (*p == ',') { ++p; continue; }
    if (*p != '"') return false;
    const char* ks = p + 1;
    const char* ke = skipString(p);
    if (!ke) return false;
    size_t klen = (size_t)(ke - 1 - ks);
    if (klen == 0 || klen >= NAME_CAP || c->ngiven >= MAX_GIVEN) return false;
    p = skipWs(ke);
    if (*p != ':') return false;
    p = skipWs(p + 1);
    GivenEntry& g = c->given[c->ngiven];
    memcpy(g.ch, ks, klen);
    g.ch[klen] = 0;
    if (strncmp(p, "true", 4) == 0) g.v = 1.0f;
    else if (strncmp(p, "false", 5) == 0) g.v = 0.0f;
    else {
      char* end = nullptr;
      g.v = strtof(p, &end);
      if (end == p || !validSensorNumber(g.v)) return false;
    }
    c->ngiven++;
    p = skipValue(p);
    if (!p) return false;
  }
}
inline bool isHex16(const char* s) {
  if (strlen(s) != 16) return false;
  for (; *s; ++s)
    if (!((*s >= '0' && *s <= '9') || (*s >= 'a' && *s <= 'f') || (*s >= 'A' && *s <= 'F')))
      return false;
  return true;
}
}  // namespace coredetail

// Parse one command line. Returns nullptr on success or an error code ("BAD_COMMAND"). `out->id`
// and `out->hasId` are filled even when parsing fails so the caller can still ack.
inline const char* parseCommand(const char* line, Command* out) {
  *out = Command();
  const char* p = jsondetail::skipWs(line);
  if (*p != '{') return "BAD_COMMAND";
  out->hasId = jsonGetInt(line, "id", &out->id);
  char name[16];
  if (!jsonGetString(line, "cmd", name, sizeof name)) return "BAD_COMMAND";
  struct Row { const char* n; Cmd c; };
  static const Row rows[] = {
      {"hello", Cmd::Hello}, {"test_start", Cmd::TestStart}, {"inject", Cmd::Inject},
      {"renew", Cmd::Renew}, {"test_off", Cmd::TestOff}, {"override", Cmd::Override},
      {"override_clear", Cmd::OverrideClear}, {"reboot", Cmd::Reboot}};
  out->cmd = Cmd::Unknown;
  for (const Row& r : rows)
    if (strcmp(name, r.n) == 0) out->cmd = r.c;
  if (out->cmd == Cmd::Unknown) return "BAD_COMMAND";
  // Too long a token is left empty, which can never match (BAD_TOKEN / BAD_COMMAND later).
  jsonGetString(line, "token", out->token, sizeof out->token);
  jsonGetString(line, "ch", out->ch, sizeof out->ch);
  int32_t n;
  if (jsonGetInt(line, "ttl_ms", &n)) {
    if (n < 1 || (uint32_t)n > MAX_TTL_MS) return "BAD_COMMAND";
    out->ttl_ms = (uint32_t)n;
  }
  if (jsonGetInt(line, "ms", &n)) {
    if (n < 1 || (uint32_t)n > MAX_OVERRIDE_MS) return "BAD_COMMAND";
    out->ms = (uint32_t)n;
  }
  if (jsonFind(line, "v")) {
    if (!coredetail::getNum(line, "v", &out->v)) return "BAD_COMMAND";
    out->hasV = true;
  }
  const char* g = jsonFind(line, "given");
  if (g && !coredetail::parseGiven(g, out)) return "BAD_COMMAND";
  return nullptr;
}

// ---------------------------------------------------------------- RTC test block

constexpr uint32_t RTC_MAGIC = 0x464C5431u;  // "FLT1"

struct RtcBlock {
  uint32_t magic;
  uint32_t ttl_ms;
  char token[17];
  uint8_t ngiven;
  GivenEntry given[MAX_GIVEN];
  uint32_t sum;
};

inline uint32_t rtcSum(const RtcBlock& b) {
  RtcBlock c;
  memcpy(&c, &b, sizeof c);
  c.sum = 0;
  uint32_t h = 2166136261u;
  const uint8_t* p = (const uint8_t*)&c;
  for (size_t i = 0; i < sizeof c; ++i) { h ^= p[i]; h *= 16777619u; }
  return h;
}
inline void rtcFill(RtcBlock* b, const Command& c) {
  memset(b, 0, sizeof *b);
  b->magic = RTC_MAGIC;
  b->ttl_ms = c.ttl_ms;
  strncpy(b->token, c.token, sizeof b->token - 1);
  b->ngiven = c.ngiven;
  for (uint8_t i = 0; i < c.ngiven; ++i) b->given[i] = c.given[i];
  b->sum = rtcSum(*b);
}
inline bool rtcValid(const RtcBlock& b) {
  return b.magic == RTC_MAGIC && b.sum == rtcSum(b) && b.ngiven <= MAX_GIVEN &&
         b.token[16] == 0 && b.ttl_ms >= 1 && b.ttl_ms <= MAX_TTL_MS;
}
inline void rtcClear(RtcBlock* b) { memset(b, 0, sizeof *b); }

// ---------------------------------------------------------------- core

enum class Action : uint8_t { None, SendHello, StartTest, RestartNormal };

struct Outcome {
  bool ok = true;
  const char* err = nullptr;
  Action action = Action::None;
};

inline bool timeReached(uint32_t now, uint32_t deadline) {
  return (int32_t)(now - deadline) >= 0;  // wrap-safe
}

class Core {
 public:
  struct PendingError {
    const char* code;
    char msg[48];
  };
  static constexpr uint8_t ERRQ = 4;

  void init() {
    for (uint8_t i = 0; i < MAX_CHANNELS; ++i) {
      float n = i < CHANNEL_COUNT ? CHANNELS[i].neutral : 0.0f;
      v_[i].f = n;
      v_[i].i = (int32_t)n;
      v_[i].b = n != 0.0f;
      ov_[i] = false;
      ovVal_[i] = false;
      ovUntil_[i] = 0;
      lastA_[i] = false;
    }
    for (uint8_t r = 0; r < DISPLAY_ROWS; ++r) { rows_[r][0] = 0; lastRows_[r][0] = 0; }
    test_ = false;
    token_[0] = 0;
    nerr_ = 0;
    nrl_ = 0;
    t0_ = 0;
    now_ = 0;
    lastTel_ = 0;
    haveTel_ = false;
    dispRev_ = 0;
  }

  // -------- time
  void setNow(uint32_t ms) { now_ = ms; }
  void markSetup(uint32_t ms) { t0_ = ms; }           // user_setup() called at ms
  uint32_t rel(uint32_t ms) const { return ms - t0_; }  // ms since user_setup
  uint32_t relNow() const { return now_ - t0_; }

  // -------- test mode
  bool testMode() const { return test_; }
  void enterTest(const char* token, uint32_t ttl_ms, const GivenEntry* given, uint8_t n,
                 uint32_t now) {
    test_ = true;
    strncpy(token_, token, sizeof token_ - 1);
    token_[sizeof token_ - 1] = 0;
    deadline_ = now + ttl_ms;
    for (uint8_t i = 0; i < n; ++i) {
      int idx = indexOf(given[i].ch);
      if (idx >= 0 && CHANNELS[idx].kind == Kind::Sensor) setSensor(idx, given[i].v);
    }
  }
  bool ttlExpired(uint32_t now) const { return test_ && timeReached(now, deadline_); }

  // -------- channel access
  int indexOf(const char* id) const {
    if (!id) return -1;
    for (uint8_t i = 0; i < CHANNEL_COUNT; ++i)
      if (strcmp(CHANNELS[i].id, id) == 0) return i;
    return -1;
  }
  // Rejects (returns false, leaves the value alone) anything validSensorNumber() refuses.
  bool setSensor(int idx, float x) {
    if (!validSensorNumber(x)) return false;
    const ChannelDef& d = CHANNELS[idx];
    if (d.type == Type::Float) v_[idx].f = x;
    else if (d.type == Type::Int) v_[idx].i = (int32_t)x;
    else if (d.type == Type::Bool) v_[idx].b = x != 0.0f;
    return true;
  }

  float readFloat(const char* ch) {
    bool bad;
    int i = lookup(ch, Type::Float, Kind::Sensor, "readFloat", &bad);
    return i < 0 ? 0.0f : (bad ? CHANNELS[i].neutral : v_[i].f);
  }
  int32_t readInt(const char* ch) {
    bool bad;
    int i = lookup(ch, Type::Int, Kind::Sensor, "readInt", &bad);
    return i < 0 ? 0 : (bad ? (int32_t)CHANNELS[i].neutral : v_[i].i);
  }
  bool readBool(const char* ch) {
    bool bad;
    int i = lookup(ch, Type::Bool, Kind::Sensor, "readBool", &bad);
    return i < 0 ? false : (bad ? CHANNELS[i].neutral != 0.0f : v_[i].b);
  }
  void setBool(const char* ch, bool on) {
    bool bad;
    int i = lookup(ch, Type::Bool, Kind::Actuator, "setBool", &bad);
    if (i >= 0 && !bad) v_[i].b = on;  // an active override still wins at apply/telemetry time
  }
  void displayLine(uint8_t row, const char* text) {
    if (row >= DISPLAY_ROWS) {
      char a[8];
      snprintf(a, sizeof a, "%u", (unsigned)row);
      reportError("BAD_ROW", "displayLine", a);
      return;
    }
    char t[DISPLAY_COLS + 1];
    strncpy(t, text ? text : "", DISPLAY_COLS);
    t[DISPLAY_COLS] = 0;
    if (strcmp(t, rows_[row]) != 0) { strcpy(rows_[row], t); dispRev_++; }
  }
  void displayClear() {
    for (uint8_t r = 0; r < DISPLAY_ROWS; ++r)
      if (rows_[r][0]) { rows_[r][0] = 0; dispRev_++; }
  }
  const char* displayRow(uint8_t r) const { return r < DISPLAY_ROWS ? rows_[r] : ""; }
  uint32_t displayRevision() const { return dispRev_; }

  // Driver-side access. Drivers publish by channel id; unknown/wrong-kind ids are reported.
  void publish(const char* ch, float x, Type type) {
    int i = indexOf(ch);
    if (i < 0) { reportError("UNKNOWN_CHANNEL", "publish", ch); return; }
    if (CHANNELS[i].kind != Kind::Sensor || CHANNELS[i].type != type) {
      reportError("WRONG_TYPE", "publish", ch);
      return;
    }
    if (!setSensor(i, x)) reportError("DRIVER_ERROR", "publish: invalid value", ch);
  }
  // Effective actuator state: override if active, else the user's last setBool.
  bool actuatorOn(const char* ch) const {
    int i = indexOf(ch);
    if (i < 0 || CHANNELS[i].kind != Kind::Actuator) return false;
    return effective(i);
  }

  // -------- errors (5.4): queued for the shell, rate-limited to 1/s per code
  void reportError(const char* code, const char* fn, const char* arg) {
    for (uint8_t k = 0; k < nrl_; ++k) {
      if (strcmp(rl_[k].code, code) == 0) {
        if (!timeReached(now_, rl_[k].next)) return;
        rl_[k].next = now_ + ERROR_PERIOD_MS;
        enqueue(code, fn, arg);
        return;
      }
    }
    if (nrl_ < RL_MAX) { rl_[nrl_].code = code; rl_[nrl_].next = now_ + ERROR_PERIOD_MS; nrl_++; }
    enqueue(code, fn, arg);
  }
  bool popError(PendingError* out) {
    if (nerr_ == 0) return false;
    *out = q_[0];
    for (uint8_t i = 1; i < nerr_; ++i) q_[i - 1] = q_[i];
    nerr_--;
    return true;
  }

  // -------- overrides
  bool anyOverride() const {
    for (uint8_t i = 0; i < CHANNEL_COUNT; ++i) if (ov_[i]) return true;
    return false;
  }
  // Drop overrides that ran out. Returns true if any expired (actuator state changed).
  bool expireOverrides(uint32_t now) {
    bool any = false;
    for (uint8_t i = 0; i < CHANNEL_COUNT; ++i)
      if (ov_[i] && timeReached(now, ovUntil_[i])) { ov_[i] = false; any = true; }
    return any;
  }

  // -------- commands (5.3)
  Outcome handle(const Command& c, uint32_t now) {
    Outcome o;
    switch (c.cmd) {
      case Cmd::Hello: o.action = Action::SendHello; break;
      case Cmd::Reboot: o.action = Action::RestartNormal; break;
      case Cmd::TestStart: {
        if (!coredetail::isHex16(c.token) || c.ttl_ms == 0) return fail("BAD_COMMAND");
        for (uint8_t i = 0; i < c.ngiven; ++i) {
          int idx = indexOf(c.given[i].ch);
          if (idx < 0) return fail("UNKNOWN_CHANNEL");
          if (CHANNELS[idx].kind != Kind::Sensor) return fail("WRONG_TYPE");
        }
        o.action = Action::StartTest;
        break;
      }
      case Cmd::TestOff:
        if (!test_) return fail("NOT_IN_TEST_MODE");
        if (strcmp(c.token, token_) != 0) return fail("BAD_TOKEN");
        o.action = Action::RestartNormal;
        break;
      case Cmd::Renew:
        if (!test_) return fail("NOT_IN_TEST_MODE");
        if (strcmp(c.token, token_) != 0) return fail("BAD_TOKEN");
        if (c.ttl_ms == 0) return fail("BAD_COMMAND");
        deadline_ = now + c.ttl_ms;
        break;
      case Cmd::Inject: {
        if (!test_) return fail("NOT_IN_TEST_MODE");
        if (strcmp(c.token, token_) != 0) return fail("BAD_TOKEN");
        if (!c.hasV) return fail("BAD_COMMAND");
        int idx = indexOf(c.ch);
        if (idx < 0) return fail("UNKNOWN_CHANNEL");
        if (CHANNELS[idx].kind != Kind::Sensor) return fail("WRONG_TYPE");
        if (!setSensor(idx, c.v)) return fail("BAD_COMMAND");
        break;
      }
      case Cmd::Override: {
        if (test_) return fail("NOT_IN_RUN_MODE");
        if (!c.hasV || c.ms == 0) return fail("BAD_COMMAND");
        int idx = indexOf(c.ch);
        if (idx < 0) return fail("UNKNOWN_CHANNEL");
        if (CHANNELS[idx].kind != Kind::Actuator || CHANNELS[idx].type != Type::Bool)
          return fail("WRONG_TYPE");
        ov_[idx] = true;
        ovVal_[idx] = c.v != 0.0f;
        ovUntil_[idx] = now + c.ms;
        break;
      }
      case Cmd::OverrideClear: {
        int idx = indexOf(c.ch);
        if (idx < 0) return fail("UNKNOWN_CHANNEL");
        if (CHANNELS[idx].kind != Kind::Actuator) return fail("WRONG_TYPE");
        ov_[idx] = false;
        break;
      }
      case Cmd::Unknown: return fail("BAD_COMMAND");
    }
    return o;
  }

  // -------- output writers
  void writeAck(JsonWriter& w, int32_t id, const Outcome& o) const {
    w.beginObj();
    w.key("type"); w.str("ack");
    w.key("id"); w.i32(id);
    w.key("ok"); w.boolean(o.ok);
    if (!o.ok) { w.key("err"); w.str(o.err); }
    w.endObj();
  }
  void writeErr(JsonWriter& w, uint32_t t, const char* code, const char* msg) const {
    w.beginObj();
    w.key("type"); w.str("err");
    w.key("t"); w.u32(t);
    w.key("code"); w.str(code);
    w.key("msg"); w.str(msg);
    w.endObj();
  }
  void writeHello(JsonWriter& w) const {
    w.beginObj();
    w.key("type"); w.str("hello");
    w.key("proto"); w.i32(FL_PROTO);
    w.key("lib"); w.str(FL_LIB_VERSION);
    w.key("fw"); w.str(FW_ID);
    w.key("test"); w.boolean(test_);
    w.key("channels"); w.beginArr();
    for (uint8_t i = 0; i < CHANNEL_COUNT; ++i) w.str(CHANNELS[i].id);
    w.endArr();
    w.endObj();
  }
  // t is already relative to user_setup().
  void writeTelemetry(JsonWriter& w, uint32_t t) {
    w.beginObj();
    w.key("type"); w.str("tel");
    w.key("t"); w.u32(t);
    w.key("mode"); w.str(test_ ? "test" : (anyOverride() ? "override" : "run"));
    writeState(w, View{this});
    w.endObj();
    snapshotOutputs(t);
  }
  // Telemetry cadence: test mode every tick (the caller asks once per tick); run mode every
  // TELEMETRY_RUN_MS or when an actuator / display row differs from the last emitted line.
  bool telemetryDue(uint32_t t) const {
    if (test_) return true;
    if (!haveTel_ || (uint32_t)(t - lastTel_) >= TELEMETRY_RUN_MS) return true;
    for (uint8_t i = 0; i < CHANNEL_COUNT; ++i)
      if (CHANNELS[i].kind == Kind::Actuator && effective(i) != lastA_[i]) return true;
    for (uint8_t r = 0; r < DISPLAY_ROWS; ++r)
      if (strcmp(rows_[r], lastRows_[r]) != 0) return true;
    return false;
  }

 private:
  struct Val { float f; int32_t i; bool b; };
  struct View {
    const Core* c;
    float f(int i) const { return c->v_[i].f; }
    int32_t iv(int i) const { return c->v_[i].i; }
    bool b(int i) const { return c->v_[i].b; }
    bool eff(int i) const { return c->effective(i); }
    const char* row(int r) const { return c->rows_[r]; }
  };
  struct Limit { const char* code; uint32_t next; };
  static constexpr uint8_t RL_MAX = 8;

  bool effective(int i) const { return ov_[i] ? ovVal_[i] : v_[i].b; }
  static Outcome fail(const char* code) {
    Outcome o;
    o.ok = false;
    o.err = code;
    return o;
  }
  void snapshotOutputs(uint32_t t) {
    lastTel_ = t;
    haveTel_ = true;
    for (uint8_t i = 0; i < CHANNEL_COUNT; ++i) lastA_[i] = effective(i);
    for (uint8_t r = 0; r < DISPLAY_ROWS; ++r) strcpy(lastRows_[r], rows_[r]);
  }
  void enqueue(const char* code, const char* fn, const char* arg) {
    if (nerr_ >= ERRQ) return;
    PendingError& e = q_[nerr_++];
    e.code = code;
    if (arg) snprintf(e.msg, sizeof e.msg, "%s(\"%s\")", fn, arg);
    else snprintf(e.msg, sizeof e.msg, "%s", fn);
  }
  // Same contract as the native fake HAL (D-060): unknown id -> -1; wrong type/kind -> index with
  // *mismatch set so the caller returns the channel's neutral value.
  int lookup(const char* ch, Type type, Kind kind, const char* fn, bool* mismatch) {
    *mismatch = false;
    int i = indexOf(ch);
    if (i < 0) { reportError("UNKNOWN_CHANNEL", fn, ch ? ch : ""); return -1; }
    if (CHANNELS[i].type != type || CHANNELS[i].kind != kind) {
      reportError("WRONG_TYPE", fn, ch);
      *mismatch = true;
    }
    return i;
  }

  Val v_[MAX_CHANNELS];
  bool ov_[MAX_CHANNELS];
  bool ovVal_[MAX_CHANNELS];
  uint32_t ovUntil_[MAX_CHANNELS];
  bool lastA_[MAX_CHANNELS];
  char rows_[DISPLAY_ROWS][DISPLAY_COLS + 1];
  char lastRows_[DISPLAY_ROWS][DISPLAY_COLS + 1];
  uint32_t dispRev_ = 0;
  bool test_ = false;
  char token_[17] = {0};
  uint32_t deadline_ = 0;
  uint32_t now_ = 0, t0_ = 0, lastTel_ = 0;
  bool haveTel_ = false;
  PendingError q_[ERRQ];
  uint8_t nerr_ = 0;
  Limit rl_[RL_MAX];
  uint8_t nrl_ = 0;
};

}  // namespace fl
