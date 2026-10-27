import { afterEach, describe, expect, it, vi } from "vitest";

import { HealthStatus } from "@/components/feedback/health-status";
import { render, screen } from "../../../tests/test-utils";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("HealthStatus", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows 'checking…' while the request is pending", () => {
    vi.stubGlobal("fetch", vi.fn().mockReturnValue(new Promise(() => {})));
    render(<HealthStatus />);
    expect(screen.getByRole("status")).toHaveTextContent("checking…");
  });

  it("shows 'backend ok' inside a polite status region", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse({ ok: true }))
    );
    render(<HealthStatus />);
    expect(await screen.findByText("backend ok")).toBeInTheDocument();
    const status = screen.getByRole("status");
    expect(status).toHaveAttribute("aria-live", "polite");
    expect(status).toHaveTextContent("backend ok");
  });

  it("shows 'backend down' when fetch rejects", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("offline")));
    render(<HealthStatus />);
    expect(await screen.findByText("backend down")).toBeInTheDocument();
  });

  it("shows 'backend down' when ok is false", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse({ ok: false }))
    );
    render(<HealthStatus />);
    expect(await screen.findByText("backend down")).toBeInTheDocument();
  });

  it("shows 'backend down' on a 500", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({}, 500)));
    render(<HealthStatus />);
    expect(await screen.findByText("backend down")).toBeInTheDocument();
  });

  it("shows 'backend down' on a schema-invalid body", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ foo: 1 })));
    render(<HealthStatus />);
    expect(await screen.findByText("backend down")).toBeInTheDocument();
  });
});
