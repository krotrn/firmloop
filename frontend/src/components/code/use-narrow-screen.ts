"use client";

import * as React from "react";

const NARROW_QUERY = "(max-width: 767px)";

function subscribe(onChange: () => void): () => void {
  const media = window.matchMedia(NARROW_QUERY);
  media.addEventListener("change", onChange);
  return () => media.removeEventListener("change", onChange);
}

export function useNarrowScreen(): boolean {
  return React.useSyncExternalStore(
    subscribe,
    () => window.matchMedia(NARROW_QUERY).matches,
    () => false
  );
}
