"use client";

import { useQuery } from "@tanstack/react-query";

import { diffVersions } from "@/services/session-versions.service";

export function useVersionDiff(id: string, against: string | null) {
  return useQuery({
    queryKey: ["versions", id, "diff", against] as const,
    queryFn: () => diffVersions(id, against as string),
    enabled: against !== null,
  });
}
