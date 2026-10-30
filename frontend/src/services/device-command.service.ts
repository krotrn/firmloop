import { env } from "@/config/env";
import { fetcher } from "@/lib/fetcher";
import type { components } from "@/types/generated/api";

export type CommandOut = components["schemas"]["CommandOut"];

export type SetSensorCommand = components["schemas"]["SetSensorCommand"];

export const deviceCommandService = {
  setSensor: (
    channel: string,
    value: SetSensorCommand["value"]
  ): Promise<CommandOut> => {
    const body: SetSensorCommand = { action: "set_sensor", channel, value };
    return fetcher<CommandOut>(
      `${env.NEXT_PUBLIC_API_URL}/api/device/command`,
      {
        method: "POST",
        body: JSON.stringify(body),
      }
    );
  },
};
