#pragma once
// Extern declarations filled by generated code (ARCHITECTURE 4.5). Not for user code.
#include <stdint.h>

namespace fl {

class Driver;

enum class Type : uint8_t { Float, Int, Bool, Text };
enum class Kind : uint8_t { Sensor, Actuator, Display };

constexpr uint8_t MAX_CHANNELS = 32;
constexpr uint8_t DISPLAY_ROWS = 4;
constexpr uint8_t DISPLAY_COLS = 21;

struct ChannelDef {
  const char* id;
  Type type;
  Kind kind;
  float neutral;
};

extern const ChannelDef CHANNELS[];
extern const uint8_t CHANNEL_COUNT;
extern const char* const FW_ID;
extern Driver* const DRIVERS[];     // ESP32 only
extern const uint8_t DRIVER_COUNT;  // ESP32 only

// ESP32 runtime entry points, called from the sketch template (ARCHITECTURE 5.2).
void runtime_begin();                    // setup(): Serial, drivers, RTC test block, user_setup()
void runtime_service(uint32_t now_ms);   // loop(): poll, serial, tick, apply, telemetry

}  // namespace fl
