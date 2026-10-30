"use client";

import * as React from "react";

import { useEvents } from "@/hooks/use-events";
import type { DeviceTelemetryEvent } from "@/types/generated/events";

/** The latest `device.telemetry` event, or null before the first one arrives. */
export function useTelemetry(): DeviceTelemetryEvent | null {
  const [telemetry, setTelemetry] = React.useState<DeviceTelemetryEvent | null>(
    null
  );
  useEvents((event) => {
    if (event.type === "device.telemetry") setTelemetry(event);
  });
  return telemetry;
}
