"use client";

import * as React from "react";

const DARK_QUERY = "(prefers-color-scheme: dark)";

function subscribe(onChange: () => void): () => void {
  const media = window.matchMedia(DARK_QUERY);
  media.addEventListener("change", onChange);
  const observer = new MutationObserver(onChange);
  observer.observe(document.documentElement, {
    attributes: true,
    attributeFilter: ["class"],
  });
  return () => {
    media.removeEventListener("change", onChange);
    observer.disconnect();
  };
}

const isDark = () =>
  document.documentElement.classList.contains("dark") ||
  window.matchMedia(DARK_QUERY).matches;

/** Monaco theme name that matches the page's light or dark mode. */
export function useEditorTheme(): "vs-dark" | "light" {
  const dark = React.useSyncExternalStore(subscribe, isDark, () => false);
  return dark ? "vs-dark" : "light";
}
