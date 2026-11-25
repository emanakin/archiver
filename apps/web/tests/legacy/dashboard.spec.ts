import { test, expect } from "@playwright/test";

test("all dashboard routes render without sample-only controls or overflow", async ({ page }, info) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  for (const [route, title] of [["/", "Overview"], ["/sources", "Sources"], ["/candidates", "Candidates"], ["/queue", "Queue"], ["/ledger", "Ledger"], ["/activity", "Activity"], ["/settings", "Settings"]]) {
    await page.goto(route);
    await expect(page.getByRole("heading", { name: title, exact: true })).toBeVisible();
    await expect(page.getByText("Prototype tools", { exact: true })).toHaveCount(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  }
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Overview", exact: true })).toBeVisible();
  await page.screenshot({ path: `test-results/${info.project.name}-overview.png`, fullPage: true });
  expect(errors).toEqual([]);
});

test("source collection and reviewed enqueue persist through reload", async ({ page, request }, info) => {
  const account = (await (await request.get("/api/v1/accounts")).json()).items.find((item: { mode: string }) => item.mode === "mock");
  const origin = { Origin: "http://localhost:8080" };
  await request.patch(`/api/v1/accounts/${account.id}/pause`, { headers: origin, data: { paused: true } });
  await page.goto(`/sources?account=${account.id}`);
  await page.getByRole("button", { name: "Add source", exact: true }).click();
  const dialog = page.getByRole("dialog");
  const handle = `qa.${info.project.name}.${Date.now()}`;
  const label = `QA ${info.project.name} ${Date.now()}`;
  await dialog.getByLabel("Instagram handle").fill(handle);
  await dialog.getByLabel("Friendly label").fill(label);
  await dialog.getByRole("button", { name: "Add source", exact: true }).click();
  await expect(dialog).not.toBeVisible();
  await page.reload();
  const card = page.locator(".source-card").filter({ has: page.getByRole("heading", { name: label, exact: true }) });
  await expect(card).toBeVisible();
  // A keyboard-accessible schedule edit persists, then is disabled before running.
  await card.getByRole("button", { name: `Actions for ${label}` }).click();
  await page.getByRole("menuitem", { name: "Collection schedule" }).click();
  const schedule = page.getByRole("dialog", { name: "Daily source collection" });
  await schedule.getByRole("switch", { name: "Enable daily collection" }).check();
  await schedule.getByLabel("Daily collection time").fill("23:50");
  await schedule.getByRole("button", { name: "Save schedule" }).click();
  await expect(schedule).not.toBeVisible();
  await page.reload();
  await expect(card.getByText(/Daily 23:50/)).toBeVisible();
  await card.getByRole("button", { name: `Actions for ${label}` }).click();
  await page.getByRole("menuitem", { name: "Collection schedule" }).click();
  await schedule.getByRole("switch", { name: "Enable daily collection" }).uncheck();
  await schedule.getByRole("button", { name: "Save schedule" }).click();
  await expect(schedule).not.toBeVisible();
  await card.getByRole("button", { name: "Collect candidates", exact: true }).click();
  await expect(card.getByText(/Collection queued/)).toBeVisible();
  await page.getByRole("button", { name: "Resume queue", exact: true }).click();
  await expect(card.getByText("Partial", { exact: true })).toBeVisible({ timeout: 25000 });
  await page.getByRole("button", { name: "Pause queue", exact: true }).click();
  await card.getByRole("button", { name: `View candidates from ${label}` }).click();
  await page.getByRole("checkbox", { name: /^Select (?!all eligible)/ }).and(page.locator(":enabled")).first().check();
  await page.getByRole("button", { name: /^Queue follows/ }).first().click();
  await page.getByRole("button", { name: "Queue 1 job", exact: true }).click();
  await expect(page.getByText("1 sample job added to the queue.", { exact: true })).toBeVisible();
  await page.reload();
  await page.goto(`/queue?account=${account.id}`);
  await expect(page.getByRole("heading", { name: "Queue", exact: true })).toBeVisible();
  await expect(page.getByText("Queued", { exact: true }).filter({ visible: true }).first()).toBeVisible();
  const jobs = (await (await request.get(`/api/v1/jobs?account_id=${account.id}`)).json()).items;
  const queued = jobs.find((job: { kind: string; state: string }) => job.kind === "follow" && job.state === "queued");
  expect(queued).toBeTruthy();
  const relationships = (await (await request.get(`/api/v1/relationships?account_id=${account.id}&target_ids=${queued.target_id}`)).json()).items;
  const relationship = relationships.find((item: { target_id: string }) => item.target_id === queued.target_id);
  expect(relationship.observed_state).toBe("none");
  expect(relationship.ever_followed).toBe(false);
  await page.locator(".identity-button").filter({ hasText: relationship.username }).filter({ visible: true }).first().click();
  const detail = page.getByRole("dialog", { name: "A closer look at the job" });
  await expect(detail.getByText("No external execution attempts recorded.", { exact: true })).toBeVisible();
  await detail.getByRole("button", { name: "Cancel queued job" }).click();
  // Detail remains visible after this terminal job disappears from the queued list.
  await expect(detail.getByText("Cancelled", { exact: true })).toBeVisible();
  await expect(detail.getByText(queued.id, { exact: true }).first()).toBeVisible();
  await detail.getByRole("button", { name: "Close dialog" }).focus();
  await page.keyboard.press("Enter");
  await expect(detail).not.toBeVisible();
});

test("API error recovery and URL-backed navigation work", async ({ page }) => {
  await page.route("**/api/v1/accounts", route => route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ error: { code: "UNAVAILABLE", message: "Temporary test outage" } }) }));
  await page.goto("/");
  await expect(page.locator("main[role='alert']")).toContainText("Temporary test outage");
  await page.unroute("**/api/v1/accounts");
  await page.getByRole("button", { name: "Try again", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Overview", exact: true })).toBeVisible();
  await page.goto("/queue?tab=completed&kind=follow");
  await expect(page.getByRole("heading", { name: "Queue", exact: true })).toBeVisible();
  await page.getByRole("tab", { name: /^Needs review/ }).click();
  await expect(page).toHaveURL(/tab=review/);
  await page.goBack();
  await expect(page).toHaveURL(/tab=completed/);
  await expect(page.getByRole("tab", { name: /^Completed/ })).toHaveAttribute("aria-selected", "true");
});

test("settings save and protection version conflicts work against the server", async ({ page, request }) => {
  const account = (await (await request.get("/api/v1/accounts")).json()).items.find((item: { mode: string }) => item.mode === "mock");
  const origin = { Origin: "http://localhost:8080" };
  const original = await (await request.get(`/api/v1/accounts/${account.id}/settings`)).json();
  const days = original.retention_days === 21 ? 22 : 21;
  await page.goto(`/settings?account=${account.id}`);
  await expect(page.getByLabel("Retention period in days")).toHaveValue(String(original.retention_days));
  await page.getByLabel("Retention period in days").fill(String(days));
  await page.getByRole("button", { name: "Save changes", exact: true }).click();
  await expect(page.getByText("Your preferences are saved.", { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByLabel("Retention period in days")).toHaveValue(String(days));
  await page.getByLabel("Retention period in days").fill("23");
  await page.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(page.getByLabel("Retention period in days")).toHaveValue(String(days));
  const all = (await (await request.get(`/api/v1/relationships?account_id=${account.id}&target_ids=demo-0`)).json()).items;
  const record = all.find((item: { target_id: string }) => item.target_id === "demo-0");
  expect(record).toBeTruthy();
  await page.goto(`/ledger?account=${account.id}&q=${encodeURIComponent(record.username)}`);
  const toggle = page.getByRole("switch", { name: `Protect ${record.username}`, exact: true }).filter({ visible: true });
  await expect(toggle).toBeVisible();
  await request.patch(`/api/v1/relationships/${record.id}`, { headers: origin, data: { expected_version: record.version, protected: !record.protected } });
  await toggle.click();
  await expect(page.getByText("This record changed. Refresh to apply your update.", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Refresh record", exact: true }).click();
  if (record.protected) await expect(toggle).not.toBeChecked(); else await expect(toggle).toBeChecked();
  const current = await (await request.get(`/api/v1/accounts/${account.id}/settings`)).json();
  const { version: _version, ...values } = original;
  await request.patch(`/api/v1/accounts/${account.id}/settings`, { headers: origin, data: { ...values, expected_version: current.version } });
});

test("switching workspace discards local settings drafts without saving them", async ({ page, request }) => {
  const accounts = (await (await request.get("/api/v1/accounts")).json()).items;
  const mock = accounts.find((item: { mode: string }) => item.mode === "mock");
  const real = accounts.find((item: { mode: string }) => item.mode === "live");
  test.skip(!mock || !real, "Requires separate mock and real workspace metadata; no Instagram calls");
  const before = await (await request.get(`/api/v1/accounts/${real.id}/settings`)).json();
  await page.goto(`/settings?account=${mock.id}`);
  await page.getByLabel("Retention period in days").fill("99");
  await page.getByRole("combobox", { name: "Workspace account" }).click();
  await page.getByRole("option", { name: `@${real.username} · live`, exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`account=${real.id}`));
  await expect(page.getByLabel("Retention period in days")).toHaveValue(String(before.retention_days));
  await expect(page.getByRole("button", { name: "Save changes", exact: true })).toBeDisabled();
  expect(await (await request.get(`/api/v1/accounts/${real.id}/settings`)).json()).toEqual(before);
});
