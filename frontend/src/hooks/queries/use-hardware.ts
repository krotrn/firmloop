"use client";

import { useQuery } from "@tanstack/react-query";

import { hardwareService } from "@/services/hardware.service";

export const hardwareQueryKeys = {
  all: ["hardware"] as const,
};

export function useHardware() {
  return useQuery({
    queryKey: hardwareQueryKeys.all,
    queryFn: () => hardwareService.getHardware(),
    // The registry is read once at backend startup and never changes while it runs.
    staleTime: Infinity,
  });
}
