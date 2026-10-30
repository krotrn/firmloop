import { z } from "zod";

import { env } from "@/config/env";
import { eventsSchema } from "@/types/generated/events-schema";
import type { ServerEvent } from "@/types/generated/events";

// Built once from the committed JSON Schema so there is no hand-written copy of the
// event models.
const serverEventValidator = z.fromJSONSchema({
  $ref: "#/$defs/ServerEvent",
  $defs: eventsSchema.$defs,
} as unknown as Parameters<typeof z.fromJSONSchema>[0]);

/** Validates one raw WebSocket message; anything invalid or unknown is dropped with a warning. */
export function parseServerEvent(raw: string): ServerEvent | null {
  let data: unknown;
  try {
    data = JSON.parse(raw);
  } catch {
    console.warn("Ignoring WebSocket message that is not JSON");
    return null;
  }
  const result = serverEventValidator.safeParse(data);
  if (!result.success) {
    console.warn("Ignoring invalid WebSocket message", data);
    return null;
  }
  return result.data as ServerEvent;
}

/** The WebSocket endpoint that belongs to the REST API at `apiUrl`. */
export function wsUrl(apiUrl: string = env.NEXT_PUBLIC_API_URL): string {
  const url = new URL(apiUrl);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  url.pathname = "/ws";
  return url.toString();
}
