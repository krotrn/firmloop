import { expect, test } from "@playwright/test";

test.describe("Home Page", () => {
  test("renders the FirmLoop title", async ({ page }) => {
    await page.goto("/");
    await expect(page).toHaveTitle(/FirmLoop/);
    await expect(
      page.getByText("Prompt to firmware, in a loop.")
    ).toBeVisible();
  });
});
