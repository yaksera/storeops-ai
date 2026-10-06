import { expect, test } from "@playwright/test";

test("fraud hold goes through approvals with keyboard shortcuts and lands in the audit log", async ({
  page,
}) => {
  await page.goto("/signup");
  await page.getByLabel("Email").fill(`approver-${Date.now()}@northbound.example`);
  await page.getByLabel("Password").fill("correct horse battery");
  await page.getByRole("button", { name: "Create account" }).click();
  await page.getByRole("button", { name: "Launch demo store" }).click();
  await expect(page).toHaveURL(/\/app\/[0-9a-f-]{36}$/, { timeout: 30_000 });
  const base = page.url();

  await page.getByRole("button", { name: "Trigger scenario" }).click();
  await page.getByRole("menuitem", { name: "Fraud attempt" }).click();
  await expect(page.getByText(/Needs approval: Hold order/).first()).toBeVisible();

  await page.goto(`${base}/approvals`);
  const card = page
    .getByRole("article")
    .filter({ hasText: /Hold order #\d+ for review/ })
    .first();
  await expect(card).toBeVisible();
  await expect(card.getByText(/billed in [A-Z]{2} but shipping to US/).first()).toBeVisible();
  const title = await card.getByRole("heading").innerText();

  await card.focus();
  await page.keyboard.press("a");
  // Execution happens on the worker; the toast confirms either stage.
  await expect(page.getByText(new RegExp(`^(Approved|Done): ${title}$`))).toBeVisible();

  await page.getByRole("tab", { name: "History" }).click();
  await expect(page.getByRole("article").filter({ hasText: title }).getByText("Done")).toBeVisible();

  await page.goto(`${base}/audit`);
  await page.getByLabel("Filter by action").selectOption("action.");
  await expect(page.getByRole("cell", { name: title }).first()).toBeVisible();
});

test("agent settings can switch autonomy and run a sample event", async ({ page }) => {
  await page.goto("/signup");
  await page.getByLabel("Email").fill(`settings-${Date.now()}@northbound.example`);
  await page.getByLabel("Password").fill("correct horse battery");
  await page.getByRole("button", { name: "Create account" }).click();
  await page.getByRole("button", { name: "Launch demo store" }).click();
  await expect(page).toHaveURL(/\/app\/[0-9a-f-]{36}$/, { timeout: 30_000 });
  await page.goto(`${page.url()}/agents`);

  const fraud = page.getByRole("article").filter({ hasText: "Fraud Guard" });
  await fraud.getByRole("radio", { name: "Auto" }).click();
  await expect(page.getByText("Fraud Guard updated")).toBeVisible();
  await expect(fraud.getByRole("radio", { name: "Auto" })).toHaveAttribute("aria-checked", "true");

  await fraud.getByRole("button", { name: "Test with a sample event" }).click();
  await expect(fraud.getByText("nothing was saved")).toBeVisible();
  await expect(
    page.getByRole("article").filter({ hasText: "Pricing Advisor" }).getByRole("radio", { name: "Auto" }),
  ).toBeDisabled();
});
