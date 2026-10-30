"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";

import { useEvents } from "@/hooks/use-events";
import { deviceService, type DeviceStatus } from "@/services/device.service";

export const deviceQueryKeys = {
  status: ["device"] as const,
};

/** The board status; a `device.status` event replaces the cached value. */
export function useDevice() {
  const queryClient = useQueryClient();

  useEvents((event) => {
    if (event.type !== "device.status") return;
    const status: DeviceStatus = {
      ...event.status,
      busy: event.status.busy ?? false,
    };
    queryClient.setQueryData(deviceQueryKeys.status, status);
  });

  return useQuery({
    queryKey: deviceQueryKeys.status,
    queryFn: () => deviceService.getDevice(),
  });
}
