import { env } from "@/config/env";
import { fetcher } from "@/lib/fetcher";
import type { components } from "@/types/generated/api";

export type Hardware = components["schemas"]["HardwareOut"];

export type HardwareChannel = components["schemas"]["ChannelOut"];

export type HardwareComponent = components["schemas"]["ComponentOut"];

export const hardwareService = {
  getHardware: (): Promise<Hardware> =>
    fetcher<Hardware>(`${env.NEXT_PUBLIC_API_URL}/api/hardware`),
};
