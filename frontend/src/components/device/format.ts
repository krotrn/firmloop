import type { HardwareChannel } from "@/services/hardware.service";

function decimals(step: number | null): number {
  if (step === null || Number.isInteger(step)) return 0;
  const text = String(step);
  const dot = text.indexOf(".");
  return dot === -1 ? 0 : text.length - dot - 1;
}

/** A channel value with its unit, using the channel's resolution for the digits. */
export function formatChannelValue(
  value: number,
  channel: HardwareChannel
): string {
  const text = value.toFixed(decimals(channel.resolution));
  return channel.unit ? `${text} ${channel.unit}` : text;
}

/** The channel's neutral value as a number (booleans become 0 or 1). */
export function neutralValue(channel: HardwareChannel): number {
  const { neutral } = channel;
  if (typeof neutral === "number") return neutral;
  if (typeof neutral === "boolean") return neutral ? 1 : 0;
  return 0;
}
