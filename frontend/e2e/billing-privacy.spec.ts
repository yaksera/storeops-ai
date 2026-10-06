import { expect, test } from "@playwright/test";

test("demo store switches plans instantly and shows usage", async ({ page }) => {
  await page.goto("/signup");
  await page.getByLabel("Email").fill(`billing-${Date.now()}@northbound.example`);
  await page.getByLabel("Password").fill("correct horse battery");
  await page.getByRole("button", { name: "Create account" }).click();
  await page.getByRole("button", { name: "Launch demo store" }).click();
  await expect(page).toHaveURL(/\/app\/[0-9a-f-]{36}$/, { timeout: 30_000 });

  await page.goto(`${page.url()}/billing`);
  await expect(page.getByRole("heading", { name: "Plan & usage" })).toBeVisible();
  await expect(page.getByText("Demo · unmetered")).toBeVisible();
  await expect(page.getByRole("meter", { name: "AI actions" })).toBeVisible();

  await page.getByRole("button", { name: "Choose Pro" }).click();
  await expect(page.getByText("Switched to the pro plan")).toBeVisible();
  await expect(page.getByText(/^Pro plan/)).toBeVisible();
});

test("privacy policy and unsubscribe pages are public", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("link", { name: "Privacy policy" }).click();
  await expect(page.getByRole("heading", { name: "Privacy policy" })).toBeVisible();
  await expect(page.getByText(/permanently deleted 48 hours later/)).toBeVisible();

  await page.goto("/unsubscribe?t=not-a-real-token");
  await page.getByRole("button", { name: "Unsubscribe" }).click();
  await expect(page.getByText("This unsubscribe link is invalid")).toBeVisible();
});
