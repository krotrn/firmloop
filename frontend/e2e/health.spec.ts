import { expect, test } from "@playwright/test";

const HEALTH_TEXT = /checking…|backend ok|backend degraded|\w+ down/;

test.describe("Backend health status", () => {
  test("home page renders the polite status region (no backend required)", async ({
    page,
  }) => {
    await page.goto("/");
    const status = page.getByRole("status").filter({ hasText: HEALTH_TEXT });
    await expect(status).toBeVisible();
    await expect(status).toHaveAttribute("aria-live", "polite");
  });
});
