import { defineConfig, devices } from "@playwright/test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
const configPath = resolve(process.cwd(), "../../.env.integration");
const values = Object.fromEntries(readFileSync(configPath, "utf8").replace(/^\uFEFF/, "").split(/\r?\n/).filter(line => line.includes("=")).map(line => { const at = line.indexOf("="); return [line.slice(0, at), line.slice(at + 1).replace(/^'(.*)'$/, "$1")]; }));
export default defineConfig({
  testDir: "./tests/e2e", fullyParallel: false, workers: 1, timeout: 45000,
  use: { baseURL: "http://localhost:8080", httpCredentials: { username: values.DASHBOARD_USER, password: values.DASHBOARD_TEST_PASSWORD }, trace: "retain-on-failure" },
  projects: [{ name: "desktop", use: { ...devices["Desktop Chrome"] } }, { name: "mobile", use: { ...devices["iPhone 13"], defaultBrowserType: "chromium" } }],
});
