"use client";

import { useQuery } from "@tanstack/react-query";

import { getVersion } from "@/services/versions.service";

export const versionQueryKey = (id: string) => ["versions", id] as const;

export function useVersion(id: string | null) {
  return useQuery({
    queryKey: versionQueryKey(id ?? ""),
    queryFn: () => getVersion(id as string),
    enabled: id !== null,
  });
}
