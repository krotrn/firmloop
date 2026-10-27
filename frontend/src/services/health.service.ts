import { z } from "zod";

import { env } from "@/config/env";
import { fetcher } from "@/lib/fetcher";

// TODO(task 6.1): replace this hand-written schema with the generated type.
export const healthSchema = z.looseObject({ ok: z.boolean() });
export type Health = z.infer<typeof healthSchema>;

export const healthService = {
  getHealth: async (): Promise<Health> => {
    const body = await fetcher<unknown>(
      `${env.NEXT_PUBLIC_API_URL}/api/health`
    );
    return healthSchema.parse(body);
  },
};
