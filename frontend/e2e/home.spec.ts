import { expect, test } from "@playwright/test";

test.describe("Sessions page", () => {
  test("renders the sessions heading and New session button", async ({
    page,
  }) => {
    await page.goto("/");
    await expect(page).toHaveTitle(/FirmLoop/);
    await expect(page.getByRole("heading", { name: "Sessions" })).toBeVisible();
    await expect(
      page.getByRole("button", { name: "New session" }).first()
    ).toBeVisible();
  });
});
