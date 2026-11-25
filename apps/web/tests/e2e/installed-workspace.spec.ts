import { test, expect } from "@playwright/test";

// Read-only acceptance of the actual local stack. Forbid browser mutations so
// this readiness check can never initiate an Instagram connection or action.
test("installed workspace exposes real onboarding and functional or paused routes", async ({ page, request }, info) => {
  await page.route("**/api/**", route => {
    expect(route.request().method()).toBe("GET");
    return route.continue();
  });
  const accountsResponse = await request.get("/api/v1/accounts?mode=live");
  expect(accountsResponse.ok()).toBe(true);
  const accounts = (await accountsResponse.json()).items;
  expect(accounts.length).toBeGreaterThan(0);
  expect(accounts.every((account: { mode: string }) => account.mode === "live")).toBe(true);
  const capability = await request.get("/api/v1/onboarding-capability");
  const capabilityValue = await capability.json();
  expect(capabilityValue.available).toBe(true);
  expect(["read_only", "live"]).toContain(capabilityValue.transport_mode);
  const id = accounts[0].id;
  const connected = accounts[0].session_state === "connected" && !accounts[0].restricted;
  const usage = async () => (await (await request.get(`/api/v1/overview?account_id=${id}`)).json()).requests_used;
  const before = await usage();
  for (const path of ["/", "/settings", "/sources", "/candidates", "/queue", "/ledger", "/activity", "/content"]) {
    await page.goto(`${path}?account=${id}`);
    await expect(page.locator("#main-content")).toBeVisible();
    await expect(page.getByRole("heading", { name: /Your workspace is unavailable|Application error/ })).toHaveCount(0);
    await expect(page.getByText(/Fictional|@archive_demo|Simulation account|backend command/)).toHaveCount(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    if (["/sources", "/candidates"].includes(path)) {
      const heading = connected ? (path === "/sources" ? "Sources" : "Candidates") : "This page is paused";
      await expect(page.getByRole("heading", { name: heading, exact: true })).toBeVisible();
    }
  }
  await page.goto(`/settings?account=${id}`);
  if (connected) {
    await page.getByRole("button", { name: "Reconnect account", exact: true }).click();
  }
  await expect(page.getByLabel("Instagram username", { exact: true })).toHaveValue(accounts[0].username);
  await expect(page.getByLabel("Password", { exact: true })).toHaveValue("");
  await expect(page.getByLabel("Verification code", { exact: true })).toHaveCount(0);
  await page.screenshot({ path: `test-results/${info.project.name}-installed-settings.png`, fullPage: true });
  expect(await usage()).toBe(before);
});

test("installed connected account exposes saved insight observations without dispatching reads", async ({ page, request }, info) => {
  await page.route("**/api/**", route => {
    expect(route.request().method()).toBe("GET");
    return route.continue();
  });
  const accounts = (await (await request.get("/api/v1/accounts?mode=live")).json()).items;
  const account = accounts.find((value: { session_state: string; restricted: boolean }) => value.session_state === "connected" && !value.restricted);
  test.skip(!account, "Requires an owner-connected account; this test never performs login");
  await page.goto(`/insights?account=${account.id}`);
  await expect(page.getByRole("heading", { name: "Insights", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Saved observations", exact: true })).toBeVisible();
  await expect(page.getByText("Observations could not be loaded. Use Refresh to try again.", { exact: true })).toHaveCount(0);
  await expect(page.locator(".insight-history-list")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: `../../artifacts/screenshots/${info.project.name}-installed-insights.png`, fullPage: true });
});
