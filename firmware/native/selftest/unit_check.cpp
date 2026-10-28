// Native unit check for fl_json.h (writer/reader) and fake_hal (error recording, channel table).
// Built and run by run.sh (not under seccomp). Exit code 0 = all good.
#include <stdio.h>
#include <string.h>

#include "FirmLoop.h"
#include "FirmLoopInternal.h"
#include "fake_hal.h"
#include "fl_json.h"

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

static void test_writer() {
  char buf[128];
  fl::JsonWriter w(buf, sizeof buf);
  w.beginObj();
  w.key("t"); w.u32(1050);
  w.key("f"); w.f32(30.14f, 1);
  w.key("b"); w.boolean(true);
  w.key("s"); w.str("a\"b\\c\n");
  w.key("arr"); w.beginArr(); w.i32(-3); w.str("x"); w.endArr();
  w.endObj();
  CHECK(!w.overflow());
  CHECK_STR(w.c_str(), "{\"t\":1050,\"f\":30.1,\"b\":true,\"s\":\"a\\\"b\\\\c\\n\",\"arr\":[-3,\"x\"]}");

  char tiny[8];
  fl::JsonWriter o(tiny, sizeof tiny);
  o.beginObj(); o.key("long_key_here"); o.i32(1); o.endObj();
  CHECK(o.overflow());
  CHECK(strlen(tiny) < sizeof tiny);
}

static void test_reader() {
  const char* line =
      "{\"id\":7,\"cmd\":\"inject\",\"ch\":\"temp\",\"v\":32.5,\"on\":true,"
      "\"given\":{\"temp\":25.0,\"motion\":false}}";
  int32_t id = 0; float v = 0; bool on = false; char s[16];
  CHECK(fl::jsonGetInt(line, "id", &id) && id == 7);
  CHECK(fl::jsonGetFloat(line, "v", &v) && v == 32.5f);
  CHECK(fl::jsonGetBool(line, "on", &on) && on);
  CHECK(fl::jsonGetString(line, "cmd", s, sizeof s) && strcmp(s, "inject") == 0);
  CHECK(!fl::jsonGetInt(line, "missing", &id));
  CHECK(!fl::jsonGetString(line, "cmd", s, 4));  // does not fit
  // nested object: lookups on the object start do not see outer keys
  const char* given = fl::jsonFind(line, "given");
  CHECK(given != nullptr && *given == '{');
  float tt = 0; bool m = true;
  CHECK(fl::jsonGetFloat(given, "temp", &tt) && tt == 25.0f);
  CHECK(fl::jsonGetBool(given, "motion", &m) && !m);
  CHECK(!fl::jsonGetInt(given, "id", &id));
  // key text inside a string value must not match
  const char* tricky = "{\"a\":\"\\\"id\\\":5\",\"id\":9}";
  CHECK(fl::jsonGetInt(tricky, "id", &id) && id == 9);
}

static void test_hal() {
  fakehal::reset();
  CHECK(fl::CHANNEL_COUNT == 9);
  CHECK(fakehal::channelIndex("temp") >= 0);
  CHECK(fakehal::channelIndex("nope") < 0);
  // neutrals
  CHECK(fl::readFloat("temp") == 25.0f);
  CHECK(fl::readInt("light") == 2000);
  CHECK(!fl::readBool("motion"));
  CHECK(fakehal::errorCount() == 0);
  // set/read
  CHECK(fakehal::setSensor("temp", "31.5"));
  CHECK(fl::readFloat("temp") == 31.5f);
  CHECK(fakehal::setSensor("light", "1234"));
  CHECK(fl::readInt("light") == 1234);
  CHECK(fakehal::setSensor("motion", "1"));
  CHECK(fl::readBool("motion"));
  CHECK(fakehal::errorCount() == 0);
  // actuators
  fl::setBool("fan", true);
  CHECK(fakehal::boolValue(fakehal::channelIndex("fan")));
  // unknown channel
  CHECK(fl::readFloat("tmp") == 0.0f);
  CHECK(fakehal::errorCount() == 1);
  CHECK_STR(fakehal::errorText(0), "UNKNOWN_CHANNEL: readFloat(\"tmp\")");
  // wrong type
  fakehal::clearErrors();
  CHECK(fl::readInt("temp") == 25);
  CHECK(fakehal::errorCount() == 1);
  CHECK_STR(fakehal::errorText(0), "WRONG_TYPE: readInt(\"temp\")");
  fakehal::clearErrors();
  fl::setBool("temp", true);
  CHECK(fakehal::errorCount() == 1);
  CHECK_STR(fakehal::errorText(0), "WRONG_TYPE: setBool(\"temp\")");
  // wrong-type read of an existing channel returns that channel's neutral (5.4)
  fakehal::clearErrors();
  fakehal::setSensor("temp", "40.0");
  CHECK(fl::readInt("temp") == 25);
  CHECK(fl::readBool("light") == true);   // neutral 2000 -> true
  CHECK(fl::readFloat("light") == 2000.0f);
  CHECK(fl::readFloat("fan") == 0.0f);    // actuator is not readable; neutral 0
  CHECK(fakehal::errorCount() == 4);
  CHECK_STR(fakehal::errorText(3), "WRONG_TYPE: readFloat(\"fan\")");
  fakehal::clearErrors();
  // bad row
  fakehal::clearErrors();
  fl::displayLine(4, "x");
  CHECK(fakehal::errorCount() == 1);
  CHECK_STR(fakehal::errorText(0), "BAD_ROW: displayLine(4)");
  // display: truncation to 21 chars and clear
  fakehal::clearErrors();
  fl::displayLine(1, "0123456789012345678901234");
  CHECK_STR(fakehal::displayRow(1), "012345678901234567890");
  fl::displayClear();
  CHECK_STR(fakehal::displayRow(1), "");
  CHECK(fakehal::errorCount() == 0);
  // clock
  fakehal::setNow(1234);
  CHECK(fl::now() == 1234u);
  // identical error de-duplicated within one tick
  fakehal::clearErrors();
  fl::readBool("zzz"); fl::readBool("zzz");
  CHECK(fakehal::errorCount() == 1);
  // harness-side SET of unknown channel records an error and returns false
  fakehal::clearErrors();
  CHECK(!fakehal::setSensor("nope", "1"));
  CHECK(fakehal::errorCount() == 1);
}

int main() {
  test_writer();
  test_reader();
  test_hal();
  if (g_fail) { printf("unit_check: %d failure(s)\n", g_fail); return 1; }
  printf("unit_check: ok\n");
  return 0;
}
