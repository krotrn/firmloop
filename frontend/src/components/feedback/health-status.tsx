"use client";

import type { ReactNode } from "react";

import { Badge } from "@/components/ui/badge";
import { useHealthQuery } from "@/hooks/queries/use-health";

export function HealthStatus() {
  const { data, isPending, isError } = useHealthQuery();

  let badge: ReactNode;
  if (isPending) {
    badge = <Badge variant="secondary">checking…</Badge>;
  } else if (isError || !data.ok) {
    badge = <Badge variant="destructive">backend down</Badge>;
  } else {
    badge = <Badge>backend ok</Badge>;
  }

  return (
    <div role="status" aria-live="polite">
      {badge}
    </div>
  );
}
