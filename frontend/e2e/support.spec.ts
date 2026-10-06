import { expect, test } from "@playwright/test";

test("angry customer is handed over and a human reply resolves it", async ({ page }) => {
  await page.goto("/signup");
  await page.getByLabel("Email").fill(`support-${Date.now()}@northbound.example`);
  await page.getByLabel("Password").fill("correct horse battery");
  await page.getByRole("button", { name: "Create account" }).click();
  await page.getByRole("button", { name: "Launch demo store" }).click();
  await expect(page).toHaveURL(/\/app\/[0-9a-f-]{36}$/, { timeout: 30_000 });
  const base = page.url();

  await page.getByRole("button", { name: "Trigger scenario" }).click();
  await page.getByRole("menuitem", { name: "Angry review" }).click();
  await expect(page.getByText("Scenario finished: Angry review")).toBeVisible();

  await page.goto(`${base}/support`);
  await expect(page.getByText(/Handed over: customer is upset/)).toBeVisible();
  await page.getByLabel("Reply").fill("We're so sorry. A replacement is on its way today.");
  await page.getByRole("button", { name: "Send and resolve" }).click();
  await expect(page.getByText("Reply sent")).toBeVisible();

  await page.getByRole("tab", { name: /Resolved/ }).click();
  await expect(page.getByRole("button", { name: /refund now/ })).toBeVisible();

  await page.goto(`${base}/insights`);
  await expect(page.getByText("Quality & defects")).toBeVisible();
});
