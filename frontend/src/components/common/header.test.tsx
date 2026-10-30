import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import React from "react";
import { describe, expect, it, vi } from "vitest";

import { Header } from "@/components/common/header";

let pathname = "/";
vi.mock("next/navigation", () => ({ usePathname: () => pathname }));
vi.mock("@/components/feedback/health-status", () => ({
  HealthStatus: () => null,
}));

const renderHeader = (path: string) => {
  pathname = path;
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <Header />
    </QueryClientProvider>
  );
};

describe("Header", () => {
  it("links to sessions, hardware and settings", () => {
    renderHeader("/");
    const nav = screen.getByRole("navigation", { name: "Main" });
    expect(nav.querySelectorAll("a")).toHaveLength(3);
    expect(screen.getByRole("link", { name: "Hardware" })).toHaveAttribute(
      "href",
      "/hardware"
    );
    expect(screen.getByRole("link", { name: "Settings" })).toHaveAttribute(
      "href",
      "/settings"
    );
  });

  it("keeps an accessible name on the logo link", () => {
    renderHeader("/");
    expect(screen.getByRole("link", { name: "FirmLoop" })).toHaveAttribute(
      "href",
      "/"
    );
  });

  it("lets the nav wrap so a long badge cannot widen the page", () => {
    renderHeader("/");
    const nav = screen.getByRole("navigation", { name: "Main" });
    expect(nav).toHaveClass("flex-wrap", "min-w-0");
  });

  it("marks the current page", () => {
    renderHeader("/settings");
    expect(screen.getByRole("link", { name: "Settings" })).toHaveAttribute(
      "aria-current",
      "page"
    );
    expect(screen.getByRole("link", { name: "Sessions" })).not.toHaveAttribute(
      "aria-current"
    );
  });

  it("keeps Sessions active inside a session", () => {
    renderHeader("/sessions/abc");
    expect(screen.getByRole("link", { name: "Sessions" })).toHaveAttribute(
      "aria-current",
      "page"
    );
  });
});
