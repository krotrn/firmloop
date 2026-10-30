"use client";

import type { ReactNode } from "react";

import { Badge } from "@/components/ui/badge";
import { useHealthQuery } from "@/hooks/queries/use-health";

export function HealthStatus() {
  const { data, isPending, isError } = useHealthQuery();

  let badge: ReactNode;
  if (isPending) {
    badge = <Badge variant="secondary">checking…</Badge>;
  } else if (isError) {
    badge = <Badge variant="destructive">backend down</Badge>;
  } else if (!data.ok) {
    // The backend answered, so name what is down instead of blaming the backend itself.
    const down = Object.entries(data.services ?? {})
      .filter(([, state]) => state === "down")
      .map(([name]) => name);
    badge = (
      <Badge variant="destructive">
        {down.length > 0 ? `${down.join(", ")} down` : "backend degraded"}
      </Badge>
    );
  } else {
    badge = <Badge>backend ok</Badge>;
  }

  return (
    <div role="status" aria-live="polite">
      {badge}
    </div>
  );
}
