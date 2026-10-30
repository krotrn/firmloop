import { env } from "@/config/env";
import { fetcher } from "@/lib/fetcher";
import type { components } from "@/types/generated/api";

export type DeviceStatus = components["schemas"]["DeviceStatus"];

export const deviceService = {
  getDevice: (): Promise<DeviceStatus> =>
    fetcher<DeviceStatus>(`${env.NEXT_PUBLIC_API_URL}/api/device`),
};
