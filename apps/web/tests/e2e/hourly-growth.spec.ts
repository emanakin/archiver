import { test, expect } from "@playwright/test";

test("installed pacing presets show saved limits and preview a new pace", async ({ page, request }, info) => {
  await page.route("**/api/**", route => {
    expect(route.request().method()).toBe("GET");
    return route.continue();
  });
  const accounts = (await (await request.get("/api/v1/accounts?mode=live")).json()).items;
  const account = accounts.find((item: { username: string }) => item.username === "truantfather");
  expect(account).toBeTruthy();
  const settings = await (await request.get("/api/v1/accounts/" + account.id + "/settings")).json();
  await page.goto(`/settings?account=${account.id}`);
  await expect(page.getByRole("heading", { name: "Settings", exact: true })).toBeVisible({ timeout: 30000 });
  await expect(page.getByLabel("Configured follows / day", { exact: true })).toHaveValue(String(settings.follows_per_day ?? ""));
  await expect(page.getByLabel("Minimum follows / hour", { exact: true })).toHaveValue(String(settings.follows_per_hour_min));
  await expect(page.getByLabel("Maximum follows / hour", { exact: true })).toHaveValue(String(settings.follows_per_hour_max));
  await expect(page.getByLabel("HTTP requests / day", { exact: true })).toHaveValue(String(settings.request_ceiling ?? ""));
  await page.getByRole("combobox", { name: "Follow pace preset" }).click();
  await page.getByRole("option", { name: "6+ months" }).click();
  await expect(page.getByRole("slider", { name: "Pace within preset" })).toHaveValue("150");
  await page.getByRole("slider", { name: "Pace within preset" }).press("End");
  await expect(page.getByText("200 follows / day · 20 / hour")).toBeVisible();
  await expect(page.getByRole("button", { name: "Save changes", exact: true })).toBeEnabled();
  await page.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(page.getByRole("button", { name: "Save changes", exact: true })).toBeDisabled();
  await expect(page.getByText("Eligible candidates are selected automatically using your saved selection and pacing settings.", { exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: `../../artifacts/screenshots/${info.project.name}-hourly-settings.png`, fullPage: true });
  await page.goto(`/candidates?account=${account.id}`);
  await expect(page.getByRole("heading", { name: "Candidates", exact: true })).toBeVisible({ timeout: 30000 });
  await expect(page.getByText(/Last 60 minutes:/)).toBeVisible();
  await expect(page.getByText(new RegExp("Target " + settings.follows_per_hour_min + "–" + settings.follows_per_hour_max + " / hour"))).toBeVisible();
  await expect(page.getByText("All eligible candidates", { exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: `../../artifacts/screenshots/${info.project.name}-hourly-candidates.png`, fullPage: true });
});
