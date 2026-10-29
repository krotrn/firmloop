// Hand-written fixture matching the codegen output exactly (sorted by id, FW_ID "native").
// The real file is produced by backend/src/firmloop/hardware/codegen.py; this one exists so the
// native self-tests do not depend on the backend.
#include "FirmLoopInternal.h"

namespace fl {
const ChannelDef CHANNELS[] = {
    {"button", Type::Bool, Kind::Sensor, 0.0f},
    {"buzzer", Type::Bool, Kind::Actuator, 0.0f},
    {"display", Type::Text, Kind::Display, 0.0f},
    {"fan", Type::Bool, Kind::Actuator, 0.0f},
    {"humidity", Type::Float, Kind::Sensor, 50.0f},
    {"led", Type::Bool, Kind::Actuator, 0.0f},
    {"light", Type::Int, Kind::Sensor, 2000.0f},
    {"motion", Type::Bool, Kind::Sensor, 0.0f},
    {"temp", Type::Float, Kind::Sensor, 25.0f},
};
const uint8_t CHANNEL_COUNT = sizeof(CHANNELS) / sizeof(CHANNELS[0]);
const char* const FW_ID = "native";
}  // namespace fl
