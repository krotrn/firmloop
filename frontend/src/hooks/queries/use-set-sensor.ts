"use client";

import { useMutation } from "@tanstack/react-query";

import { deviceCommandService } from "@/services/device-command.service";

export interface SetSensorVariables {
  channel: string;
  value: boolean | number;
}

export function useSetSensor() {
  return useMutation({
    mutationFn: ({ channel, value }: SetSensorVariables) =>
      deviceCommandService.setSensor(channel, value),
  });
}
