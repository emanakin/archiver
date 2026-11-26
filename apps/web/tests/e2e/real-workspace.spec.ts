import { test, expect, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

// All API traffic is handled here. These browser contract tests cannot dispatch
// synthetic credentials to the real Instagram worker.
async function backend(page: Page, options: { empty?: boolean; connected?: boolean; unavailable?: boolean; failure?: boolean; twoAccounts?: boolean; approval?: boolean; writeTransport?: boolean; contentTimeout?: boolean; pollState?: "backoff" | "needs_attention" | "needs_conversation"; reelShare?: boolean } = {}) {
  const id = "00000000-0000-4000-8000-000000000001";
  const account = { id, username: "synthetic.owner", instagram_id: null, paused: true, mode: "live", created_at: new Date().toISOString(), session_state: options.connected ? "connected" : "disconnected", restricted: false, cooldown_until: null, automatic_pause_reason: null, live_acquisition_enabled: false, live_cleanup_enabled: false, workflow_version: 1, read_only_verified_at: null };
  const preferences = { follows_per_day: 0, unfollows_per_day: 0, request_ceiling: 20, collection_batch_size: 5, jobs_per_run: 10, window_start: "00:00", window_end: "00:00", retention_days: 15, pending_request_policy: "manual_review", timezone: "America/Toronto", version: 1 };
  const second = { ...account, id: "00000000-0000-4000-8000-000000000002", username: "second.owner", instagram_id: "555" };
  const state = { exists: !options.empty, progress: null as null | Record<string, unknown>, passwords: 0, codes: 0, holdOverview: false, releaseOverview: () => {}, checks: [] as Record<string, unknown>[], failInsight: false, watches: [] as Record<string, unknown>[], cases: [] as Record<string, unknown>[], drafts: [] as Record<string, unknown>[], operations: [] as Record<string, unknown>[] };
  const empty = { items: [], total: 0, limit: 100, offset: 0 };
  let library = {version:1,playlists:[] as {playlist_id:string;label:string;enabled:boolean;unused:number;sync_state:string}[]};
  let dmInbox: Record<string, unknown> | null = null;
  let dmPoll: Record<string, unknown> | null = null;
  const dmSubmissions: Record<string, unknown>[] = options.reelShare ? [{
    id: "reel-one", state: "reel_ready", reel_url: "https://www.instagram.com/reel/SYNTHETIC/",
    reel_caption: "taking the runway a little less seriously.\n\n#meme #runway",
  }] : [];
  await page.route("**/api/v1/**", async route => {
    const request = route.request();
    const url = new URL(request.url());
    const selected = url.searchParams.get("account_id") === second.id || url.pathname.includes(second.id) ? second : account;
    const path = url.pathname.replace("/api/v1", "").replace(second.id, id);
    const respond = (value: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(value) });
    if (path === "/me") return respond({ auth_mode: "legacy", email: null, workspace_id: null });
    if (path === "/content-operations") return respond({ ...empty, items: state.operations, total: state.operations.length });
    if (path.endsWith("/spotify-playlist")) return respond(null);
    if (path.endsWith("/music-library")) {
      if(request.method()==="PUT"){
        const body=request.postDataJSON();
        expect(body.expected_version).toBe(library.version);
        library={version:library.version+1,playlists:body.playlists.map((p: object)=>({...p,unused:0,sync_state:"pending"}))};
      }
      return respond(library);
    }
    if (path.endsWith("/audience")) return respond({ following: 2, requested: 1, mutual: 1, checked_following: 2, last_observation: null });
    if (path.endsWith("/saved-audio")) return respond([]);
    if (path.endsWith("/dm-inboxes")) return respond(dmInbox ? [dmInbox] : []);
    if (path.endsWith("/dm-inbox/direct-publication")) {
      expect(request.method()).toBe("PUT");
      expect(dmInbox).not.toBeNull();
      dmInbox = { ...dmInbox, direct_publish: request.postDataJSON().enabled,
        poll_state: options.pollState ?? "scheduled",
        last_poll_error: options.pollState === "needs_conversation" ? "DM_CONVERSATION_MISSING" : options.pollState ? "READ_FAILED" : null,
        next_poll_at: options.pollState === "backoff" ? new Date(Date.now() + 300000).toISOString() : null };
      return respond(dmInbox);
    }
    if (path.endsWith("/dm-inbox")) {
      if (request.method() === "GET") return respond(dmInbox);
      expect(request.postDataJSON()).toEqual({ sender_account_id: second.id, enabled: true });
      dmInbox = { sender_id: "555", sender_username: second.username, enabled: true, last_polled_at: null, has_older: false, poll_interval_seconds: 60, feed_autopost_default: true, local_captions: true };
      return respond(dmInbox);
    }
    if (path.endsWith("/dm-inbox/poll")) {
      dmPoll = { kind: "dm_poll", id: "dm-poll-one", state: "succeeded" };
      const stamp = new Date().toISOString();
      dmSubmissions.push({ id: "dm-one", draft_id: "draft-one", media_ref: "https://www.instagram.com/p/SYNTHETIC/", state: "draft_ready", caption_status: "applied" });
      state.drafts.push({ id: "draft-one", account_id: id, kind: "feed_photo", caption: "chip em", revision: 1, status: "draft", updated_at: stamp, preview_url: "/api/v1/content-drafts/draft-one/preview?revision=1" });
      return respond(dmPoll, 202);
    }
    if (path === "/dm-submissions") return respond({ ...empty, items: dmSubmissions, total: dmSubmissions.length });
    if (path === "/jobs" && url.searchParams.get("kind") === "dm_poll") return respond({ ...empty, items: dmPoll ? [dmPoll] : [] });
    const publication = path.match(/^\/content-drafts\/([^/]+)\/publish$/);
    if (publication) {
      const draft = state.drafts.find(item => item.id === publication[1])!;
      expect(request.postDataJSON()).toEqual({ expected_revision: draft.revision, reviewed: true });
      const item = { id: draft.id === "draft-one" ? "publication-one" : "publication-story", account_id: id, kind: draft.kind === "story_photo" ? "publish_story" : "publish_photo", state: options.contentTimeout ? "unknown" : "queued", dispatch_started_at: options.contentTimeout ? new Date().toISOString() : null, error: options.contentTimeout ? "OUTCOME_UNKNOWN" : null, payload: { draft_id: draft.id, revision: draft.revision } };
      state.operations.push(item); Object.assign(draft, { status: options.contentTimeout ? "unknown" : "queued", publication_job_id: item.id });
      return respond(item, 202);
    }
    if (path === "/content-drafts/draft-one/story-draft") {
      const source = state.drafts[0];
      expect(source.status).toBe("published");
      expect(request.postDataJSON()).toEqual({ expected_revision: source.revision });
      let story = state.drafts.find(item => item.source_draft_id === source.id);
      if (!story) {
        story = { ...source, id: "story-one", source_draft_id: source.id, kind: "story_photo", caption: "", status: "draft", revision: 1, media_id: null, publication_job_id: null, preview_url: "/api/v1/content-drafts/story-one/preview?revision=1" };
        state.drafts.push(story);
      }
      return respond(story);
    }
    if (path === "/content-drafts/story-one") return respond(state.drafts.find(item => item.id === "story-one"));
    if (path === "/content-drafts/story-one/preview") return route.fulfill({ contentType: "image/png", body: readFileSync(resolve(process.cwd(), "tests/fixtures/preview-story.png")) });
    if (path === "/content-drafts/draft-one/reconcile") {
      expect(request.postDataJSON()).toEqual({ job_id: "publication-one", media_ref: "https://www.instagram.com/p/ACTUAL/" });
      const item = { id: "reconciliation-one", account_id: id, kind: "reconcile_content", state: "queued", dispatch_started_at: null, error: null, payload: { draft_id: "draft-one", parent_id: "publication-one" } };
      state.operations.push(item); return respond(item, 202);
    }
    if (path === "/content-drafts/draft-one/media-action") {
      const body = request.postDataJSON();
      expect(body.expected_revision).toBe(state.drafts[0].revision);
      expect(body.reviewed).toBe(true);
      expect(body.idempotency_key).toEqual(expect.any(String));
      const item = { id: "media-action-one", account_id: id, kind: body.operation, state: "queued", dispatch_started_at: null, error: null, payload: { draft_id: "draft-one", media_id: "987_999", caption: body.caption ?? "" } };
      state.operations.push(item); return respond(item, 202);
    }
    if (path === "/insight-watches") {
      if (request.method() === "GET") return respond({ ...empty, items: state.watches, total: state.watches.length });
      const item = { ...request.postDataJSON(), id: "watch-one", enabled: false, version: 1, next_due_at: new Date().toISOString(), first_success_at: null, last_success_at: null, created_at: new Date().toISOString() };
      state.watches.push(item); return respond(item, 201);
    }
    if (path === "/insight-watches/watch-one" && request.method() === "PATCH") {
      expect(request.postDataJSON().expected_version).toBe(state.watches[0].version);
      Object.assign(state.watches[0], request.postDataJSON(), { version: Number(state.watches[0].version) + 1 });
      return respond(state.watches[0]);
    }
    if (path === "/insight-watches/watch-one/observations") return respond({ ...empty, total: state.watches[0]?.enabled ? 2 : 0, items: state.watches[0]?.enabled ? [{ target_id: "101", username: "baseline.user", baseline: true, first_seen_at: new Date().toISOString(), last_seen_at: new Date().toISOString() }, { target_id: "102", username: "newly.observed", baseline: false, first_seen_at: new Date().toISOString(), last_seen_at: new Date().toISOString() }] : [] });
    if (path === "/relationship-tests") {
      if (request.method() === "GET") return respond({ ...empty, items: state.cases, total: state.cases.length });
      const item = { id: "case-one", account_id: id, target_id: "123", username: "test.target", before_check_id: request.postDataJSON().check_id, state: "prepared", follow_job_id: null, unfollow_job_id: null, error_code: null, authorized_at: null, expires_at: null, completed_at: null, created_at: new Date().toISOString() };
      state.cases.push(item); return respond(item, 201);
    }
    if (path === `/accounts/${id}/content-assets`) return respond({ id: "asset-one", account_id: id, mime: "image/png", width: 160, height: 100, byte_size: request.postDataBuffer()?.length ?? 0, created_at: new Date().toISOString() }, 201);
    if (path === "/content-drafts") {
      if (request.method() === "GET") return respond({ ...empty, items: state.drafts, total: state.drafts.length });
      const item = { ...request.postDataJSON(), id: "draft-one", caption: "", fit_mode: "fit", rotation: 0, overlay_text: "", text_position: "bottom", revision: 1, status: "draft", media_id: null, publication_job_id: null, created_at: new Date().toISOString(), updated_at: new Date().toISOString(), preview_url: "/api/v1/content-drafts/draft-one/preview?revision=1" };
      state.drafts.push(item); return respond(item, 201);
    }
    if (path === "/content-drafts/draft-one" && request.method() === "GET") return respond(state.drafts[0]);
    if (path === "/content-drafts/draft-one" && request.method() === "DELETE") {
      expect(Number(url.searchParams.get("revision"))).toBe(state.drafts[0].revision);
      state.drafts.shift();
      return route.fulfill({ status: 204 });
    }
    if (path === "/content-drafts/draft-one" && request.method() === "PATCH") {
      expect(request.postDataJSON().expected_revision).toBe(state.drafts[0].revision);
      const revision = Number(state.drafts[0].revision) + 1;
      Object.assign(state.drafts[0], request.postDataJSON(), { revision, updated_at: new Date().toISOString(), preview_url: `/api/v1/content-drafts/draft-one/preview?revision=${revision}` });
      if (request.postDataJSON().photo_order) {
        state.drafts[0].photos = request.postDataJSON().photo_order.map((number: number) => ({number, preview_url: `/api/v1/content-drafts/draft-one/preview?revision=${revision}&photo=${number}`}));
      }
      return respond(state.drafts[0]);
    }
    if (path === "/content-drafts/draft-one/preview") return route.fulfill({ contentType: "image/png", body: readFileSync(resolve(process.cwd(), `tests/fixtures/preview-${state.drafts[0]?.kind === "story_photo" ? "story" : "photo"}.png`)) });
    if (path === "/insight-checks") {
      if (request.method() === "GET") return respond({ ...empty, items: state.checks.filter(check => check.account_id === url.searchParams.get("account_id")), total: state.checks.length });
      const body = request.postDataJSON();
      const check = { ...body, id: `check-${state.checks.length}`, job_id: "insight-job", state: "queued", error_code: null, requests_used: 0, profile: null as unknown, relationship: null as unknown, items: [] as unknown[], next_cursor: null, complete: null, note: "Waiting for a real account observation.", observed_at: null as null | string, created_at: new Date().toISOString() };
      state.checks.unshift(check);
      setTimeout(() => {
        if (state.failInsight) Object.assign(check, { state: "failed", error_code: "SOURCE_INACCESSIBLE", requests_used: 1 });
        else Object.assign(check, { state: "succeeded", requests_used: 2, observed_at: new Date().toISOString(), complete: false,
          note: "Bounded observation. Completeness and exact like times are unavailable.",
          profile: ["profile", "relationship"].includes(body.kind) ? { id: "123", username: body.target, followers: 12, following: 14, posts: 3, is_private: false } : null,
          relationship: body.kind === "relationship" ? { target_id: "123", state: "none", follows_back: false } : null,
          items: ["profile", "relationship"].includes(body.kind) ? [] : body.kind === "posts" ? [{ id: "456", kind: "post", media_type: "Photo", caption: "An observed post", url: "https://www.instagram.com/p/ABC/", likes: 5, comments: 2 }] : [{ id: "123", kind: "user", username: "observed.user", url: "https://www.instagram.com/observed.user/" }],
        });
      }, 250);
      return respond(check, 202);
    }
    if (path === "/accounts") {
      expect(url.searchParams.get("mode")).toBe("live");
      return respond({ ...empty, items: state.exists ? options.twoAccounts ? [account, second] : [account] : [], total: state.exists ? options.twoAccounts ? 2 : 1 : 0 });
    }
    if (path === "/onboarding-capability") return respond({ available: !options.unavailable, transport_mode: "read_only", reason: options.unavailable ? "Account connection is temporarily unavailable while the Instagram worker is offline." : null });
    if (path === "/account-connections" && request.method() === "POST") {
      expect(request.postDataJSON().password).toBe("synthetic-private-password");
      state.passwords++;
      state.exists = true;
      state.progress = { id: "test-connection", account_id: id, state: "connecting", challenge_id: null, challenge_kind: null, error_code: null, expires_at: new Date(Date.now() + 600000).toISOString(), message: "Contacting Instagram. Verification will appear here if requested." };
      setTimeout(() => {
        Object.assign(state.progress!, options.approval ? { state: "awaiting_approval", challenge_id: "phone-challenge", challenge_kind: "approval", message: "Check Instagram on your phone and approve the login if prompted. Then continue here." } : options.failure ? { state: "failed", error_code: "PROFILE_VERIFICATION_REQUIRED", message: "Instagram returned a verification step this app could not complete. Review the prompt in Instagram, then reconnect here." } : { state: "awaiting_code", challenge_id: "synthetic-challenge", challenge_kind: "mfa", message: "Instagram requested verification. Enter the code below." });
      }, 300);
      return respond(state.progress, 202);
    }
    if (path === `/accounts/${id}/onboarding`) return respond(state.progress);
    if (path === "/account-connections/test-connection/log") return respond({ connection_id: "test-connection", state: state.progress?.state ?? "queued", error_code: null, request_count: 1, events: [
      { at: new Date().toISOString(), event: "request_started", detail: { ordinal: 1, mutation: false } },
      { at: new Date().toISOString(), event: "verification_screens", detail: { stage: "entrypoint", screens: ["com.bloks.www.synthetic.approval_screen"], session: false } },
    ] });
    if (path === "/account-connections/test-connection/approval") {
      expect(request.postDataJSON()).toEqual({ challenge_id: "phone-challenge", choice: "code" });
      Object.assign(state.progress!, { state: "awaiting_code", challenge_id: "synthetic-challenge", challenge_kind: "mfa", message: "Instagram requested verification. Enter the code below." });
      return respond(state.progress);
    }
    if (path === "/account-connections/test-connection/code") {
      expect(request.postDataJSON()).toEqual({ code: "654321", challenge_id: "synthetic-challenge" });
      state.codes++;
      Object.assign(state.progress!, { state: "connected", message: "Instagram connected. Automations remain paused." });
      account.session_state = "connected";
      return respond(state.progress);
    }
    if (path === "/account-connections/test-connection/cancel") {
      Object.assign(state.progress!, { state: "cancelled", message: "Connection cancelled. You can start again when ready." });
      return respond(state.progress);
    }
    if (path === `/accounts/${id}/settings`) {
      if (request.method() === "PATCH") Object.assign(preferences, request.postDataJSON(), { version: preferences.version + 1 });
      return respond(preferences);
    }
    if (path === `/accounts/${id}/connection`) return respond({ session_state: account.session_state, transport_mode: options.writeTransport ? "live" : "read_only", last_validated_at: account.session_state === "connected" ? new Date().toISOString() : null, library_version: "3.2.15", capabilities: {}, import_complete: false, can_collect: account.session_state === "connected", can_follow: false, can_unfollow: false });
    if (["/sources", "/candidates", "/relationships", "/jobs", "/audit-events"].includes(path)) return respond(empty);
    if (path === "/worker-status") return respond({ status: "online", last_heartbeat: new Date().toISOString(), simulated: false });
    if (path === "/overview") {
      if (state.holdOverview) await new Promise<void>(resolve => { state.releaseOverview = resolve; });
      return respond({ account: selected, mode: "live", job_counts: {}, live_metrics: null, candidates_available: 0, queued_follows: 0, unfollows_due: 0, protected_accounts: 0, follows_used: 0, unfollows_used: 0, requests_used: 0, run: { processed: 0, skipped: 0, failed: 0, review: 0 }, chart: [], next_job: null, operations: { oldest_due_at: null, oldest_due_age_seconds: null, last_relationship_observation: null, last_collection_at: null, unknown_writes: 0 } });
    }
    throw new Error(`Unhandled browser API contract: ${request.method()} ${path}`);
  });
  return state;
}

test("new account uses password then a separate code form and survives reload", async ({ page }, info) => {
  const state = await backend(page, { empty: true });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Your archive starts here." })).toBeVisible();
  await expect(page.getByLabel("Verification code", { exact: true })).toHaveCount(0);
  await page.getByLabel("Instagram username", { exact: true }).fill("synthetic.owner");
  await page.getByLabel("Password", { exact: true }).fill("synthetic-private-password");
  await page.getByRole("button", { name: "Add account and continue" }).click();
  await expect(page.getByLabel("Verification code", { exact: true })).toBeVisible();
  await expect(page).toHaveURL(/settings\?account=/);
  await page.reload();
  await expect(page.getByLabel("Verification code", { exact: true })).toBeVisible();
  await page.evaluate(() => { (document.activeElement as HTMLElement)?.blur(); window.scrollTo(0, 0); });
  await page.screenshot({ path: `test-results/${info.project.name}-verification.png`, fullPage: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.getByLabel("Verification code", { exact: true }).fill("654321");
  await page.getByRole("button", { name: "Confirm code", exact: true }).click();
  await expect(page.getByText("Instagram is connected. Automations remain under your control.")).toBeVisible();
  expect(state.passwords).toBe(1);
  expect(state.codes).toBe(1);
  await expect(page.getByText(/backend command|simulation workspace|Fictional/)).toHaveCount(0);
});

test("cancelling verification clears the code step without another password request", async ({ page }) => {
  const state = await backend(page, { empty: true });
  await page.goto("/");
  await page.getByLabel("Instagram username", { exact: true }).fill("synthetic.owner");
  await page.getByLabel("Password", { exact: true }).fill("synthetic-private-password");
  await page.getByRole("button", { name: "Add account and continue" }).click();
  await expect(page.getByLabel("Verification code", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Cancel connection", exact: true }).click();
  await expect(page.getByText("Connection cancelled. You can start again when ready.")).toBeVisible();
  await expect(page.getByLabel("Verification code", { exact: true })).toHaveCount(0);
  await expect(page.getByLabel("Password", { exact: true })).toHaveValue("");
  expect(state.passwords).toBe(1);
  expect(state.codes).toBe(0);
});

test("phone approval waits across reload before code verification without another password", async ({ page }, info) => {
  const state = await backend(page, { empty: true, approval: true });
  await page.goto("/");
  await page.getByLabel("Instagram username", { exact: true }).fill("synthetic.owner");
  await page.getByLabel("Password", { exact: true }).fill("synthetic-private-password");
  await page.getByRole("button", { name: "Add account and continue" }).click();
  await expect(page.getByRole("heading", { name: "Approve the login in Instagram", exact: true })).toBeVisible();
  await expect(page.getByLabel("Verification code", { exact: true })).toHaveCount(0);
  await page.reload();
  await expect(page.getByRole("heading", { name: "Approve the login in Instagram", exact: true })).toBeVisible();
  await page.screenshot({ path: `test-results/${info.project.name}-phone-approval.png`, fullPage: true });
  await expect(page.getByRole("button", { name: "I approved on my phone · Continue", exact: true })).toBeVisible();
  await page.getByText(/^Connection log/).click();
  await expect(page.getByText("com.bloks.www.synthetic.approval_screen", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Instagram is asking me for a code", exact: true }).click();
  await expect(page.getByLabel("Verification code", { exact: true })).toBeVisible();
  await page.getByLabel("Verification code", { exact: true }).fill("654321");
  await page.getByRole("button", { name: "Confirm code", exact: true }).click();
  await expect(page.getByText("Instagram is connected. Automations remain under your control.")).toBeVisible();
  expect(state.passwords).toBe(1);
  expect(state.codes).toBe(1);
});

test("preferences save and account switching waits without exposing the previous account", async ({ page }, info) => {
  const state = await backend(page, { connected: true, twoAccounts: true });
  await page.goto("/settings");
  await page.getByLabel("HTTP requests / day", { exact: true }).fill("25");
  await page.getByRole("button", { name: "Save changes", exact: true }).first().click();
  await expect(page.getByText("Your preferences are saved.")).toBeVisible();
  await page.reload();
  await expect(page.getByLabel("HTTP requests / day", { exact: true })).toHaveValue("25");
  state.holdOverview = true;
  await page.getByRole("combobox", { name: "Workspace account", exact: true }).click();
  await page.getByRole("option", { name: "@second.owner · live", exact: true }).click();
  await expect(page.getByRole("status", { name: "Loading workspace", exact: true })).toBeVisible();
  await expect(page.getByText("@synthetic.owner", { exact: true })).toHaveCount(0);
  state.holdOverview = false; state.releaseOverview();
  await expect(page.getByRole("combobox", { name: "Workspace account", exact: true })).toHaveText("@second.owner · live");
  await expect(page.getByLabel("Instagram username", { exact: true })).toHaveCount(0);
  if (info.project.name === "mobile") {
    await page.getByRole("button", { name: "More", exact: true }).click();
    await page.getByRole("button", { name: "Content", exact: true }).click();
  } else await page.getByRole("button", { name: "Content DRAFTS", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Start with an image" })).toBeVisible();
  await expect(page).toHaveURL(/account=00000000-0000-4000-8000-000000000002/);
});

test("music library saves, pauses and removes a playlist",async({page})=>{
  await backend(page,{connected:true});
  await page.goto("/content");
  await page.getByRole("tab",{name:"Music library",exact:true}).click();
  await page.getByRole("button",{name:"Add playlist",exact:true}).click();
  await page.getByLabel("Playlist name",{exact:true}).fill("Late rotation");
  await page.getByLabel("Spotify playlist link",{exact:true}).fill("https://open.spotify.com/playlist/4AGhXYyHt5wKFJMK6KiHYh?si=example");
  await page.getByRole("button",{name:"Save playlist",exact:true}).click();
  await expect(page.getByRole("combobox",{name:"Playlist",exact:true})).toContainText("Late rotation");
  await page.getByRole("button",{name:"Pause playlist",exact:true}).click();
  await expect(page.getByRole("button",{name:"Resume playlist",exact:true})).toBeVisible();
  await page.getByRole("button",{name:"Edit playlist",exact:true}).click();
  await page.getByRole("button",{name:"Remove from library",exact:true}).click();
  await expect(page.getByRole("combobox",{name:"Playlist",exact:true})).toHaveCount(0);
});

test("an approved DM share opens a draft and requires a separate story approval", async ({ page }) => {
  const state = await backend(page, { connected: true, twoAccounts: true, writeTransport: true });
  await page.goto("/content");
  await page.getByRole("tab", {name:"DM inbox",exact:true}).click();
  await page.getByRole("combobox", { name: "Sender account", exact: true }).click();
  await page.getByRole("option", { name: "@second.owner", exact: true }).click();
  await page.getByRole("button", { name: "Approve sender", exact: true }).click();
  await page.getByRole("button", { name: "Enable direct publishing", exact: true }).click();
  await page.getByRole("button", { name: "Enable for approved sender", exact: true }).click();
  await expect(page.getByText("Automatic inbox checks run every 60 seconds while the account queue runs.", { exact: true })).toBeVisible();
  await expect(page.getByText(/Shares autopost after a follow-up inbox check/)).toBeVisible();
  await expect(page.getByText(/Captions are generated locally/)).toBeVisible();
  await page.getByRole("button", { name: "Disable direct publishing", exact: true }).click();
  await expect(page.getByRole("button", { name: "Enable direct publishing", exact: true })).toBeEnabled();
  await page.getByRole("button", { name: "Check for new shares", exact: true }).click();
  await expect(page.getByText("DM caption applied.", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Open draft", exact: true }).click();
  await expect(page.getByLabel("Post caption", { exact: true })).toHaveValue("chip em");
  expect(state.operations).toHaveLength(0);
  await page.getByRole("button", { name: "Review publication", exact: true }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Approve this publication", exact: true }).click();
  await expect.poll(() => state.operations.map(job => job.kind)).toEqual(["publish_photo"]);
  Object.assign(state.operations[0], { state: "succeeded" });
  Object.assign(state.drafts[0], { status: "published", media_id: "987_999" });
  await page.reload();
  await page.getByRole("button", { name: "Make a story draft", exact: true }).click();
  await expect(page.getByText("Photo story from a published post", { exact: true })).toBeVisible();
  await expect(page.getByLabel("Story caption metadata", { exact: true })).toHaveValue("");
  await page.getByRole("button", { name: "Review publication", exact: true }).click();
  expect(state.operations).toHaveLength(1);
  await page.getByRole("alertdialog").getByRole("button", { name: "Approve this publication", exact: true }).click();
  await expect.poll(() => state.operations.map(job => job.kind)).toEqual(["publish_photo", "publish_story"]);
});

for (const pollState of ["backoff", "needs_attention"] as const) {
  test(`DM polling ${pollState} explains recovery without sending actions`, async ({ page }, info) => {
    const state = await backend(page, { connected: true, twoAccounts: true, writeTransport: true, pollState });
    await page.goto("/content");
    await page.getByRole("tab", { name: "DM inbox", exact: true }).click();
    await page.getByRole("combobox", { name: "Sender account", exact: true }).click();
    await page.getByRole("option", { name: "@second.owner", exact: true }).click();
    await page.getByRole("button", { name: "Approve sender", exact: true }).click();
    await page.getByRole("button", { name: "Enable direct publishing", exact: true }).click();
    await page.getByRole("button", { name: "Enable for approved sender", exact: true }).click();
    const notice = page.getByText(pollState === "backoff" ? /The last inbox read failed \(READ_FAILED\). Next automatic check:/ : /Automatic inbox checks are paused after a failed read \(READ_FAILED\)/);
    await expect(notice).toBeVisible();
    await expect(page.getByRole("button", { name: "Check for new shares", exact: true })).toBeEnabled();
    await expect(page.getByRole("button", { name: "Pause inbox", exact: true })).toBeEnabled();
    expect(state.operations).toHaveLength(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await notice.evaluate(element => element.scrollIntoView({ block: "center" }));
    await page.screenshot({ path: `../../artifacts/screenshots/${info.project.name}-dm-${pollState}.png` });
  });
}

test("DM inbox explains missing conversations and links to the original Reel", async ({ page }) => {
  const state = await backend(page, { connected: true, twoAccounts: true, writeTransport: true, pollState: "needs_conversation", reelShare: true });
  await page.goto("/content");
  await page.getByRole("tab", { name: "DM inbox", exact: true }).click();
  await page.getByRole("combobox", { name: "Sender account", exact: true }).click();
  await page.getByRole("option", { name: "@second.owner", exact: true }).click();
  await page.getByRole("button", { name: "Approve sender", exact: true }).click();
  await page.getByRole("button", { name: "Enable direct publishing", exact: true }).click();
  await page.getByRole("button", { name: "Enable for approved sender", exact: true }).click();
  await expect(page.getByText(/Instagram couldn’t find a conversation with this sender/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Check for new shares", exact: true })).toBeEnabled();
  await expect(page.getByRole("link", { name: "Open Reel on Instagram" })).toHaveAttribute("href", "https://www.instagram.com/reel/SYNTHETIC/");
  await page.getByText("Caption for your review", { exact: true }).click();
  await expect(page.getByText(/#meme #runway/)).toBeVisible();
  expect(state.operations).toHaveLength(0);
});

test("disconnected reads are paused while local content drafts remain available", async ({ page }, info) => {
  await backend(page);
  for (const path of ["/sources", "/candidates"]) {
    await page.goto(path);
    await expect(page.getByRole("heading", { name: "This page is paused" })).toBeVisible();
    await expect(page.getByText("Currently unavailable", { exact: true })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  }
  for (const [path, title] of [["/queue", "Queue"], ["/ledger", "Ledger"], ["/activity", "Activity"], ["/settings", "Settings"]]) {
    await page.goto(path);
    await expect(page.getByRole("heading", { name: title, exact: true })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  }
  await page.screenshot({ path: `test-results/${info.project.name}-account-settings.png`, fullPage: true });
});

test("worker unavailability disables connection and preserves the form", async ({ page }) => {
  await backend(page, { empty: true, unavailable: true });
  await page.goto("/");
  await expect(page.getByText(/temporarily unavailable while the Instagram worker is offline/)).toBeVisible();
  await page.getByLabel("Instagram username", { exact: true }).fill("synthetic.owner");
  await page.getByLabel("Password", { exact: true }).fill("synthetic-private-password");
  await expect(page.getByRole("button", { name: "Add account and continue" })).toBeDisabled();
});

test("route transitions keep navigation visible with a circular loading state", async ({ page }, info) => {
  const state = await backend(page, { connected: true });
  await page.goto("/settings");
  await expect(page.getByRole("heading", { name: "Settings", exact: true })).toBeVisible();
  state.holdOverview = true;
  const nav = page.getByRole("navigation", { name: info.project.name === "mobile" ? "Mobile navigation" : "Main navigation", exact: true });
  await nav.getByRole("link", { name: /^Queue/ }).click();
  await expect(page.getByRole("status", { name: "Loading page", exact: true })).toBeVisible();
  await expect(nav).toBeVisible();
  await expect(page.getByText("Loading your workspace…", { exact: true })).toHaveCount(0);
  state.holdOverview = false; state.releaseOverview();
  await expect(page.getByRole("heading", { name: "Queue", exact: true })).toBeVisible();
});

test("add-account dialog has independent labeled fields and closes without submitting", async ({ page }) => {
  const state = await backend(page);
  await page.goto("/settings");
  await page.getByRole("button", { name: "Add Instagram account", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Add an Instagram account", exact: true });
  await dialog.getByLabel("Instagram username", { exact: true }).fill("synthetic.owner");
  await dialog.getByText("Password", { exact: true }).click();
  await expect(dialog.getByLabel("Password", { exact: true })).toBeFocused();
  await dialog.getByRole("button", { name: "Close dialog", exact: true }).click();
  expect(state.passwords).toBe(0);
});

test("unsupported verification is visible instead of a missing code form", async ({ page }) => {
  const state = await backend(page, { empty: true, failure: true });
  await page.goto("/");
  await page.getByLabel("Instagram username", { exact: true }).fill("synthetic.owner");
  await page.getByLabel("Password", { exact: true }).fill("synthetic-private-password");
  await page.getByRole("button", { name: "Add account and continue" }).click();
  await expect(page.getByText(/Instagram returned a verification step this app could not complete/)).toBeVisible();
  await expect(page.getByLabel("Verification code", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Connect Instagram", exact: true }).last()).toBeDisabled();
  expect(state.passwords).toBe(1);
});

test("connected pages use real data and keep unsupported actions paused", async ({ page }) => {
  await backend(page, { connected: true });
  for (const [path, title] of [["/", "Overview"], ["/sources", "Sources"], ["/candidates", "Candidates"]]) {
    await page.goto(path);
    await expect(page.getByRole("heading", { name: title, exact: true })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  }
  await expect(page.getByText("Following is currently paused", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: /^Queue follows/ })).toBeDisabled();
  await page.goto("/settings");
  await expect(page.getByText("Follow and cleanup automations", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: /^Enable follows/ })).toHaveCount(0);
});

test("insight observations queue, complete and survive reload", async ({ page }, info) => {
  const state = await backend(page, { connected: true });
  await page.goto("/insights");
  await expect(page.getByRole("heading", { name: "Insights", exact: true })).toBeVisible();
  await page.getByRole("combobox", { name: "Observation", exact: true }).click();
  await page.getByRole("option", { name: "Accounts they follow", exact: true }).click();
  await page.getByLabel("Instagram account", { exact: true }).fill("thelawrencemendy");
  await page.getByRole("button", { name: "Queue observation", exact: true }).click();
  await expect(page.getByText("@observed.user", { exact: true })).toBeVisible();
  await expect(page.getByText(/Completeness and exact like times are unavailable/)).toBeVisible();
  await page.reload();
  await expect(page.getByText("@observed.user", { exact: true })).toBeVisible();
  expect(state.checks).toHaveLength(1);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: `test-results/${info.project.name}-insights.png`, fullPage: true });
});

test("post observations lead to an explicit liker check", async ({ page }) => {
  const state = await backend(page, { connected: true });
  await page.goto("/insights");
  await page.getByRole("combobox", { name: "Observation", exact: true }).click();
  await page.getByRole("option", { name: "Recent posts", exact: true }).click();
  await page.getByLabel("Instagram account", { exact: true }).fill("thelawrencemendy");
  await page.getByRole("button", { name: "Queue observation", exact: true }).click();
  await page.getByRole("button", { name: "Watch post likers", exact: true }).click();
  await expect(page.getByLabel("Post to watch", { exact: true })).toHaveValue("https://www.instagram.com/p/ABC/");
  await page.getByRole("button", { name: "Save liker source", exact: true }).click();
  await expect(page.getByLabel("Post or Reel URL", { exact: true })).toHaveValue("https://www.instagram.com/p/ABC/");
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "Inspect post likers", exact: true }).click();
  await expect(page.getByLabel("Instagram post URL", { exact: true })).toHaveValue("https://www.instagram.com/p/ABC/");
  await page.getByRole("button", { name: "Queue observation", exact: true }).click();
  await expect(page.getByText("@observed.user", { exact: true })).toBeVisible();
  expect(state.checks).toHaveLength(2);
  expect(state.checks[0].kind).toBe("likers");
});

test("inaccessible observations show a failure instead of empty success", async ({ page }) => {
  const state = await backend(page, { connected: true });
  state.failInsight = true;
  await page.goto("/insights");
  await page.getByRole("button", { name: "Queue observation", exact: true }).click();
  await expect(page.getByText("This observation did not complete", { exact: true })).toBeVisible();
  await expect(page.getByText(/SOURCE_INACCESSIBLE/)).toBeVisible();
  await expect(page.getByText("No records were returned in this bounded response.", { exact: true })).toHaveCount(0);
});

test("post watches start paused and preserve explicit versioned controls", async ({ page }) => {
  const state = await backend(page, { connected: true });
  await page.goto("/insights");
  await page.getByLabel("Post to watch", { exact: true }).fill("https://www.instagram.com/p/ABC/");
  await page.getByRole("button", { name: "Create paused watch", exact: true }).click();
  await expect(page.getByRole("button", { name: "Enable polling", exact: true })).toBeVisible();
  expect(state.watches[0].enabled).toBe(false);
  await page.getByRole("button", { name: "Enable polling", exact: true }).click();
  await expect(page.getByText("Baseline sample", { exact: true })).toBeVisible();
  await expect(page.getByText("Newly observed", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Pause polling", exact: true }).click();
  await page.getByRole("button", { name: "Edit watch", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Edit post watch", exact: true });
  await dialog.getByLabel("Refresh interval (minutes)", { exact: true }).fill("120");
  await dialog.getByRole("button", { name: "Save watch settings", exact: true }).click();
  await expect(dialog).not.toBeVisible();
  expect(state.watches[0]).toMatchObject({ enabled: false, interval_minutes: 120, version: 4 });
  await page.reload();
  await expect(page.getByText("Every 120 minutes · up to 5 likers per sample", { exact: true })).toBeVisible();
});

test("a prepared target test cannot execute on read-only transport", async ({ page }) => {
  const state = await backend(page, { connected: true });
  await page.goto("/insights");
  await page.getByRole("combobox", { name: "Observation", exact: true }).click();
  await page.getByRole("option", { name: "Our relationship", exact: true }).click();
  await page.getByLabel("Instagram account", { exact: true }).fill("test.target");
  await page.getByRole("button", { name: "Queue observation", exact: true }).click();
  await page.getByRole("button", { name: "Prepare selected target test", exact: true }).click();
  const dialog = page.getByRole("alertdialog", { name: "Follow then unfollow @test.target?", exact: true });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Run this paired test", exact: true })).toBeDisabled();
  expect(state.cases).toHaveLength(1);
  expect(state.cases[0].state).toBe("prepared");
});

test("photo story drafts save adjustments and reload with their preview", async ({ page }, info) => {
  const state = await backend(page, { connected: true });
  await page.goto("/content");
  await page.getByLabel("Media file", { exact: true }).setInputFiles({ name: "synthetic.png", mimeType: "image/png", buffer: Buffer.from("fixture image; intercepted locally") });
  await page.getByRole("combobox", { name: "Draft type", exact: true }).click();
  await page.getByRole("option", { name: "Photo story · 9:16", exact: true }).click();
  await page.getByRole("button", { name: "Create draft", exact: true }).click();
  await expect(page.getByRole("img", { name: "Saved draft preview", exact: true })).toBeVisible();
  await page.getByLabel("Text on image", { exact: true }).fill("A synthetic story draft");
  await page.getByLabel("Story caption metadata", { exact: true }).fill("Saved caption");
  await page.getByRole("button", { name: /Rotate left/ }).click();
  await page.getByRole("button", { name: "Save draft changes", exact: true }).click();
  await expect(page.getByRole("button", { name: "Save draft changes", exact: true })).toBeDisabled();
  expect(state.drafts[0]).toMatchObject({ kind: "story_photo", revision: 2, rotation: 90, overlay_text: "A synthetic story draft", caption: "Saved caption", status: "draft" });
  await page.reload();
  await expect(page.getByLabel("Text on image", { exact: true })).toHaveValue("A synthetic story draft");
  await expect(page.getByRole("button", { name: "Review publication", exact: true })).toBeDisabled();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: `../../artifacts/screenshots/${info.project.name}-content-draft.png`, fullPage: true });
});

test("publication requires a saved preview and explicit revision review", async ({ page }, info) => {
  const state = await backend(page, { connected: true, writeTransport: true });
  await page.goto("/content");
  await page.getByLabel("Media file", { exact: true }).setInputFiles({ name: "synthetic.png", mimeType: "image/png", buffer: Buffer.from("fixture intercepted locally") });
  await page.getByRole("button", { name: "Create draft", exact: true }).click();
  await page.getByLabel("Text on image", { exact: true }).fill("Changed composition");
  await expect(page.getByRole("button", { name: "Review publication", exact: true })).toBeDisabled();
  await expect(page.getByText("Save or discard the edits to review the saved revision.")).toBeVisible();
  await page.getByRole("button", { name: "Save draft changes", exact: true }).click();
  await page.getByRole("button", { name: "Review publication", exact: true }).click();
  const dialog = page.getByRole("alertdialog", { name: "Publish revision 2 to @synthetic.owner?", exact: true });
  await expect(dialog.getByRole("img", { name: "Publication revision preview", exact: true })).toBeVisible();
  await expect.poll(() => dialog.evaluate(element => {
    const button = element.querySelector("button:last-child")!;
    const bounds = button.getBoundingClientRect();
    return element.contains(document.elementFromPoint(bounds.left + bounds.width / 2, bounds.top + bounds.height / 2));
  })).toBe(true);
  await page.screenshot({ path: `../../artifacts/screenshots/${info.project.name}-publication-review.png` });
  expect(state.operations).toHaveLength(0);
  await dialog.getByRole("button", { name: "Approve this publication", exact: true }).click();
  await expect(dialog).not.toBeVisible();
  expect(state.operations).toHaveLength(1);
  expect(state.operations[0]).toMatchObject({ kind: "publish_photo", state: "queued", payload: { revision: 2 } });
  await page.reload();
  await expect(page.getByText("publish photo", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Review publication", exact: true })).toHaveCount(0);
});

test("uncertain publication queues a media read without reposting", async ({ page }) => {
  const state = await backend(page, { connected: true, writeTransport: true, contentTimeout: true });
  await page.goto("/content");
  await page.getByLabel("Media file", { exact: true }).setInputFiles({ name: "synthetic.png", mimeType: "image/png", buffer: Buffer.from("fixture intercepted locally") });
  await page.getByRole("button", { name: "Create draft", exact: true }).click();
  await page.getByRole("button", { name: "Review publication", exact: true }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Approve this publication", exact: true }).click();
  await page.getByLabel("Actual published Instagram URL or media ID", { exact: true }).fill("https://www.instagram.com/p/ACTUAL/");
  await page.getByRole("button", { name: "Verify outcome without reposting", exact: true }).click();
  await expect(page.getByText("reconcile content", { exact: true })).toBeVisible();
  expect(state.operations.map(job => job.kind)).toEqual(["publish_photo", "reconcile_content"]);
});

test("a carousel saves a new order and can be deleted from drafts", async ({ page }) => {
  const state = await backend(page, { connected: true, writeTransport: true });
  state.drafts.push({
    id: "draft-one", kind: "feed_carousel", status: "draft", revision: 1,
    caption: "Three photos", updated_at: new Date().toISOString(),
    preview_url: "/api/v1/content-drafts/draft-one/preview?revision=1",
    photos: [1, 2, 3].map(number => ({number, preview_url: `/api/v1/content-drafts/draft-one/preview?revision=1&photo=${number}`})),
  });
  await page.goto("/content");
  await expect(page.getByRole("img", { name: "Draft photo 1", exact: true })).toBeVisible();
  await page.getByRole("combobox", { name: "Composition", exact: true }).click();
  await page.getByRole("option", { name: "Original frame · extend edges if needed", exact: true }).click();
  await page.getByRole("button", { name: "Move photo 3 up", exact: true }).click();
  await page.getByRole("button", { name: "Move photo 3 up", exact: true }).click();
  await page.getByRole("button", { name: "Save draft changes", exact: true }).click();
  await expect.poll(() => state.drafts[0].photo_order).toEqual([3, 1, 2]);
  expect(state.drafts[0].fit_mode).toBe("native");
  await expect(page.getByRole("img", { name: "Draft photo 3", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Review publication", exact: true }).click();
  const review = page.getByRole("alertdialog");
  await expect(review.getByRole("img")).toHaveCount(3);
  await expect(review.getByRole("img").first()).toHaveAccessibleName("Photo 3");
  await review.getByRole("button", { name: "Cancel", exact: true }).click();
  await page.getByRole("button", { name: "Delete draft", exact: true }).click();
  await page.getByRole("alertdialog", { name: "Delete this draft?", exact: true })
    .getByRole("button", { name: "Delete draft", exact: true }).click();
  await expect(page.getByText("No drafts yet. Add an image to get started.", { exact: true })).toBeVisible();
  expect(state.operations).toHaveLength(0);
});

for (const [story, remove, label] of [[false, false, "caption editing"], [true, true, "story removal"], [false, true, "post removal"]] as const) {
  test(`published ${label} needs a separate approval`, async ({ page }) => {
    const state = await backend(page, { connected: true, writeTransport: true });
    await page.goto("/content");
    await page.getByLabel("Media file", { exact: true }).setInputFiles({ name: "synthetic.png", mimeType: "image/png", buffer: Buffer.from("fixture intercepted locally") });
    await page.getByRole("button", { name: "Create draft", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Adjust your draft", exact: true })).toBeVisible();
    Object.assign(state.drafts[0], { status: "published", media_id: "987_999", kind: story ? "story_photo" : "feed_photo" });
    await page.reload();
    await page.getByRole("button", { name: remove ? `Review ${story ? "story" : "post"} removal` : "Edit published caption", exact: true }).click();
    expect(state.operations).toHaveLength(0);
    if (remove) {
      const dialog = page.getByRole("alertdialog", { name: `Remove this published photo ${story ? "story" : "post"}?`, exact: true });
      await expect(dialog.getByText(/987_999/)).toBeVisible();
      await dialog.getByRole("button", { name: `Approve ${story ? "story" : "post"} removal`, exact: true }).click();
    } else {
      const dialog = page.getByRole("dialog", { name: "Change your published caption", exact: true });
      await dialog.getByLabel("Replacement post caption", { exact: true }).fill("Owner reviewed replacement");
      await dialog.getByRole("button", { name: "Approve caption change", exact: true }).click();
    }
    await expect.poll(() => state.operations.length).toBe(1);
    expect(state.operations[0].kind).toBe(remove ? (story ? "delete_story" : "delete_post") : "edit_caption");
  });
}
