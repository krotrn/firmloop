import { expect, test } from "@playwright/test";

test.describe("Backend health status", () => {
  test("home page renders the polite status region (no backend required)", async ({
    page,
  }) => {
    await page.goto("/");
    const status = page.getByRole("status");
    await expect(status).toBeVisible();
    await expect(status).toHaveAttribute("aria-live", "polite");
    await expect(status).toContainText(/checking…|backend ok|backend down/);
  });
});
