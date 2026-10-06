import { expect, test, type Page } from "@playwright/test";

async function signUpAndLaunchDemo(page: Page) {
  await page.goto("/signup");
  await page
    .getByLabel("Email")
    .fill(`e2e-${Date.now()}-${Math.random().toString(36).slice(2, 8)}@northbound.example`);
  await page.getByLabel("Password").fill("correct horse battery");
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL(/\/onboarding$/);
  await page.getByRole("button", { name: "Launch demo store" }).click();
  await expect(page).toHaveURL(/\/app\/[0-9a-f-]{36}$/, { timeout: 30_000 });
}

test("signed-out visitors are sent to sign in", async ({ page }) => {
  await page.goto("/app");
  await expect(page).toHaveURL(/\/login\?next=%2Fapp/);
});

test("live dashboard streams demo traffic and runs scenarios", async ({ page }) => {
  await signUpAndLaunchDemo(page);

  await expect(page.getByRole("heading", { name: "Live Ops" })).toBeVisible();
  await expect(page.getByRole("status").filter({ hasText: /^Live$/ })).toBeVisible();
  await expect(page.getByText("Revenue today")).toBeVisible();
  await expect(page.getByText("Fraud Guard", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "Trigger scenario" }).click();
  await page.getByRole("menuitem", { name: "Fraud attempt" }).click();
  await expect(page.getByText("Fraud attempt started")).toBeVisible();
  await expect(page.getByText("Scenario started: Fraud attempt")).toBeVisible();
  await expect(page.getByText(/billed in [A-Z]{2}/).first()).toBeVisible();
  await expect(page.getByText("Scenario finished: Fraud attempt")).toBeVisible();
});

test("demo traffic can be paused and resumed", async ({ page }) => {
  await signUpAndLaunchDemo(page);
  const pause = page.getByRole("button", { name: "Pause demo traffic" });
  await pause.click();
  await expect(page.getByRole("button", { name: "Resume demo traffic" })).toBeVisible();
  await page.getByRole("button", { name: "Resume demo traffic" }).click();
  await expect(pause).toBeVisible();
});
