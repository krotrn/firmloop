import { expect, test, type Page } from "@playwright/test";

/** A whole run (fake model, compile, native tests, virtual board) finishes well inside this. */
const RUN_TIMEOUT_MS = 180_000;

const REQUEST =
  "Turn the fan on when the temperature is above 30 C, off otherwise";

/** Opens a session, sends the request, confirms the proposed spec and waits for the run to end. */
async function runToSuccess(page: Page) {
  await page.goto("/");
  await page.getByRole("button", { name: "New session" }).first().click();
  await page.waitForURL(/\/sessions\/.+/);

  await page.getByLabel("Request").fill(REQUEST);
  await page.getByRole("button", { name: "Send" }).click();

  await page.getByRole("button", { name: "Confirm" }).click();

  // The status badge only: a timeline step can carry the same word.
  await expect(
    page.locator('[data-slot="badge"]', { hasText: /^Succeeded$/ }).first()
  ).toBeVisible({ timeout: RUN_TIMEOUT_MS });
}

test("a request becomes a verified program that the virtual board accepts commands for", async ({
  page,
}) => {
  await runToSuccess(page);

  await page.getByRole("tab", { name: "Code" }).click();
  await expect(page.locator(".monaco-editor").first()).toBeVisible();
  await expect(page.getByText("user_tick").first()).toBeVisible();

  await page.getByRole("tab", { name: "Tests" }).click();
  await expect(page.getByText(/^(\d+) of \1 passed$/)).toBeVisible();

  await page.getByRole("tab", { name: "Device" }).click();
  await expect(page.getByRole("group", { name: "fan off" })).toBeVisible();
  const command = page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/device/command") &&
      response.request().method() === "POST"
  );
  await page.getByRole("slider", { name: "temp" }).press("End");
  expect((await command).ok()).toBe(true);
  await expect(page.getByRole("group", { name: "fan on" })).toBeVisible();
});
