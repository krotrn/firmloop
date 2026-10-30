"use client";

import * as React from "react";

import { ActuatorIndicator } from "@/components/device/actuator-indicator";
import { neutralValue } from "@/components/device/format";
import { OledDisplay } from "@/components/device/oled-display";
import { SensorControl } from "@/components/device/sensor-control";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useHardware } from "@/hooks/queries/use-hardware";
import { useSetSensor } from "@/hooks/queries/use-set-sensor";
import { useTelemetry } from "@/hooks/use-telemetry";

export interface DevicePanelProps {
  mode: "real" | "virtual";
}

const DEFAULT_ROWS = 4;
const DEFAULT_COLS = 21;

export function DevicePanel({ mode }: DevicePanelProps) {
  const hardware = useHardware();
  const telemetry = useTelemetry();
  const setSensor = useSetSensor();
  const [commandError, setCommandError] = React.useState<string | null>(null);

  if (hardware.isPending) {
    return (
      <div className="flex flex-col gap-4" aria-busy="true">
        <Skeleton className="h-6 w-40" />
        <div className="grid gap-4 sm:grid-cols-2">
          <Skeleton className="h-24" />
          <Skeleton className="h-24" />
        </div>
      </div>
    );
  }

  if (hardware.isError) {
    return (
      <div role="alert" className="flex flex-col items-start gap-3">
        <p className="text-sm">
          Could not load the hardware: {hardware.error.message}
        </p>
        <Button variant="outline" onClick={() => void hardware.refetch()}>
          Retry
        </Button>
      </div>
    );
  }

  const channels = hardware.data.channels;
  const sensors = channels.filter((c) => c.kind === "sensor");
  const actuators = channels.filter((c) => c.kind === "actuator");
  const display = channels.find((c) => c.kind === "display");

  const virtual = mode === "virtual";
  const locked = telemetry !== null && telemetry.mode === "test";

  return (
    <div className="flex flex-col gap-8">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="secondary">{virtual ? "Virtual" : "Real"}</Badge>
        {telemetry?.mode === "test" ? <Badge>Test running</Badge> : null}
        {telemetry?.mode === "override" ? <Badge>Override active</Badge> : null}
        {telemetry === null ? (
          <span className="text-muted-foreground text-sm">
            Waiting for telemetry…
          </span>
        ) : null}
      </div>

      <section className="flex flex-col gap-4" aria-labelledby="device-sensors">
        <h3 id="device-sensors" className="text-sm font-medium">
          Sensors
        </h3>
        {commandError !== null ? (
          <p role="alert" className="text-destructive text-sm">
            {commandError}
          </p>
        ) : null}
        <div className="grid gap-4 sm:grid-cols-2">
          {sensors.map((channel) => (
            <SensorControl
              key={channel.id}
              channel={channel}
              value={telemetry?.s[channel.id] ?? neutralValue(channel)}
              readOnly={!virtual}
              disabled={locked}
              onCommit={(value) =>
                setSensor.mutate(
                  { channel: channel.id, value },
                  {
                    onSuccess: () => setCommandError(null),
                    onError: (error) => setCommandError(error.message),
                  }
                )
              }
            />
          ))}
        </div>
      </section>

      {actuators.length > 0 ? (
        <section
          className="flex flex-col gap-4"
          aria-labelledby="device-actuators"
        >
          <h3 id="device-actuators" className="text-sm font-medium">
            Actuators
          </h3>
          <div className="grid gap-4 sm:grid-cols-2">
            {actuators.map((channel) => (
              <ActuatorIndicator
                key={channel.id}
                id={channel.id}
                on={(telemetry?.a[channel.id] ?? 0) >= 0.5}
              />
            ))}
          </div>
        </section>
      ) : null}

      {display ? (
        <section
          className="flex flex-col gap-4"
          aria-labelledby="device-display"
        >
          <h3 id="device-display" className="text-sm font-medium">
            OLED display
          </h3>
          <OledDisplay
            rows={display.rows ?? DEFAULT_ROWS}
            cols={display.cols ?? DEFAULT_COLS}
            lines={telemetry?.d ?? []}
          />
        </section>
      ) : null}
    </div>
  );
}
