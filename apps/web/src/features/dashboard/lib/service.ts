import { api, ApiError } from "@/lib/api";
import type { components } from "@/lib/api.generated";
import type { ArchiveProvider, Snapshot, Source, Job, Settings, Relationship, CreateSourceInput, EnqueueResult, Activity, Candidate } from "./types";

type BackendAccount = components["schemas"]["AccountResponse"];
type BackendSource = components["schemas"]["SourceResponse"];
type BackendJob = components["schemas"]["JobResponse"];
export type Page<T> = { items: T[]; total: number; limit: number; offset: number };
export class ConflictError extends Error { constructor(message: string) { super(message); } }
export class RecoveryRequiredError extends Error {}
export const PAGE_SIZE = 8;
export class NoAccountsError extends Error {}

const sourceKinds = {
  profile: "profile_followers",
  mutual: "profile_mutuals",
  post: "post_likers",
} as const;

function sourceView(source: BackendSource, index = 0): Source {
  return { id: source.id, label: source.label || source.value, type: source.kind === "profile_mutuals" ? "mutual" : source.kind === "post_likers" ? "post" : "profile", value: source.value,
    enabled: source.enabled, collection_size: source.requested_limit, ordering: source.requested_order,
    last_collected_at: source.last_collected_at, discovered_count: source.discovered_count, overlap_count: source.overlap_count,
    result: source.collection_state, collection_note: source.collection_note,
    schedule_enabled: source.schedule_enabled, schedule_local_time: source.schedule_local_time, schedule_version: source.schedule_version,
    art: index % 6, category: source.kind === "profile_mutuals" ? "Mutual following" : "Source" };
}
export function jobView(job: BackendJob, mode: "mock" | "live", relationships: Relationship[]): Job {
  const labels: Partial<Record<Job["kind"], string>> = { connect: "Account connection", verify: "Session verification", insight: "Insight observation", import_following: "Following import", publish_photo: "Photo publication", publish_story: "Story publication", publish_carousel: "Carousel publication", publish_reel: "Reel publication", compose_caption: "Caption analysis", edit_caption: "Caption change", delete_story: "Story removal", delete_post: "Post removal", archive_post: "Post archive", reconcile_content: "Media outcome verification", dm_poll: "DM inbox check", dm_import: "DM media import", dm_audio: "Shared audio lookup", dm_receipt: "DM receipt", dm_replacement_music: "Replacement soundtrack selection", music_diagnostic: "Music catalog diagnostic", import_source_photo: "Source photo import" };
  return { ...job, username: relationships.find(x => x.target_id === job.target_id)?.username ?? job.target_id ?? labels[job.kind] ?? "Source collection",
    source_id: typeof job.payload.source_id === "string" ? job.payload.source_id : null, attempt_count: job.attempt_count ?? 0, mode, request_id: job.id };
}

export async function loadWorkspace(resource = "", params = new URLSearchParams(), signal?: AbortSignal): Promise<Snapshot> {
  const accounts = await api<Page<BackendAccount>>("/accounts?mode=live");
  const account = accounts.items.find(x => x.id === params.get("account")) ?? accounts.items[0];
  if (!account) throw new NoAccountsError("Add an Instagram account to get started.");
  const scope = `account_id=${encodeURIComponent(account.id)}`;
  const serverPage = Math.max(1, Number(params.get("page") || 1) || 1);
  const paging = (name: string) => {
    const query = new URLSearchParams(scope);
    query.set("limit", name === resource ? String(name === "activity" ? 10 : PAGE_SIZE) : "100");
    query.set("offset", String(name === resource ? (serverPage - 1) * (name === "activity" ? 10 : PAGE_SIZE) : 0));
    if (name !== resource) return query;
    if (params.get("q")) query.set("q", params.get("q")!);
    if (name === "sources") {
      const tab = params.get("tab");
      query.set("kind", sourceKinds[tab === "post" || tab === "mutual" ? tab : "profile"]);
    }
    if (name === "candidates") {
      if (params.get("source") && params.get("source") !== "all") query.set("source_id", params.get("source")!);
      const eligibility = params.get("eligibility") ?? "all";
      if (eligibility === "excluded") query.set("category", "excluded");
      else query.set("available", eligibility === "all" ? "active" : eligibility);
      if (params.get("relationship") && params.get("relationship") !== "all") query.set("relationship", params.get("relationship")!);
    }
    if (name === "ledger" && params.get("tab") && params.get("tab") !== "all") query.set("state", params.get("tab") === "previous" ? "previously_followed" : params.get("tab")!);
    if (name === "queue") {
      const states: Record<string, string> = { scheduled: "queued", running: "running", completed: "simulated,succeeded,skipped,cancelled" };
      const tab = params.get("tab") || "scheduled";
      if (tab === "review") query.set("needs_review", "true");
      else query.set("state", states[tab] || states.scheduled);
      for (const key of ["kind", "date"]) if (params.get(key) && params.get(key) !== "all") query.set(key, params.get(key)!);
    }
    if (name === "activity" && params.get("category") && params.get("category") !== "all") query.set("category", params.get("category")!);
    return query;
  };
  const [sources, candidates, relationships, jobs, activity, settings, overview, worker, connection] = await Promise.all([
    api<Page<BackendSource>>(`/sources?${paging("sources")}`, { signal }), api<Page<Candidate>>(`/candidates?${paging("candidates")}`, { signal }),
    api<Page<Relationship>>(`/relationships?${paging("ledger")}`, { signal }), api<Page<BackendJob>>(`/jobs?${paging("queue")}`, { signal }),
    api<Page<Activity>>(`/audit-events?${paging("activity")}`, { signal }), api<Settings & { version: number }>(`/accounts/${account.id}/settings`),
    api<Omit<Snapshot["overview"], "next_job"> & { next_job: BackendJob | null }>(`/overview?${scope}`),
    api<{ status: string; last_heartbeat: string | null }>(`/worker-status?${scope}`),
    api<components["schemas"]["ConnectionResponse"]>(`/accounts/${account.id}/connection`),
  ]);
  const selectedSource = params.get("source");
  if (selectedSource && selectedSource !== "all" && !sources.items.some(x => x.id === selectedSource)) {
    const selected = await api<BackendSource>(`/sources/${selectedSource}`, { signal });
    if (selected.account_id !== account.id) throw new Error("That source belongs to another workspace.");
    sources.items.push(selected);
  }
  if (resource === "candidates" || resource === "queue") {
    const ids = resource === "candidates" ? candidates.items.map(x => x.target_id) : jobs.items.flatMap(x => x.target_id ? [x.target_id] : []);
    if (ids.length) {
      const query = new URLSearchParams(scope); query.set("limit", "100");
      for (const id of ids) query.append("target_ids", id);
      const associated = await api<Page<Relationship>>(`/relationships?${query}`, { signal });
      relationships.items = [...new Map([...relationships.items, ...associated.items].map(x => [x.id, x])).values()];
    }
  }
  return { accounts: accounts.items, connection, account: { ...account, restricted: account.restricted ?? false, cooldown_until: account.cooldown_until && Date.parse(account.cooldown_until) > Date.now() ? account.cooldown_until : null, session_state: account.session_state ?? "disconnected", pause_reason: account.automatic_pause_reason ?? null },
    sources: sources.items.map(sourceView), candidates: candidates.items, relationships: relationships.items,
    jobs: jobs.items.map(job => jobView(job, account.mode, relationships.items)), activity: activity.items,
    settings, last_synced_at: new Date().toISOString(), fixture: "normal",
    overview: { ...overview, next_job: overview.next_job ? jobView(overview.next_job, account.mode, relationships.items) : null },
    pagination: { sources: sources.total, candidates: candidates.total, ledger: relationships.total, queue: jobs.total, activity: activity.total },
    worker_status: worker.status,
    worker_last_heartbeat: worker.last_heartbeat,
  };
}

export class HttpProvider implements ArchiveProvider {
  constructor(private state: Snapshot, private refresh: () => Promise<void>) {}
  subscribe = () => () => {};
  getSnapshot = () => this.state;
  getOverview = async () => this.state.overview;
  listSources = async () => this.state.sources;
  listCandidates = async () => this.state.candidates;
  listRelationships = async () => {
    const ids = [...new Set(this.state.relationships.map(x => x.target_id))];
    const records: Relationship[] = [];
    for (let offset = 0; offset < ids.length; offset += 100) {
      const query = new URLSearchParams({ account_id: this.state.account.id, limit: "100" });
      for (const id of ids.slice(offset, offset + 100)) query.append("target_ids", id);
      records.push(...(await api<Page<Relationship>>(`/relationships?${query}`)).items);
    }
    return records;
  };
  listJobs = async () => this.state.jobs;
  listActivity = async () => this.state.activity;
  getSettings = async () => api<Settings>(`/accounts/${this.state.account.id}/settings`);
  private async mutate<T>(path: string, body?: unknown, method = "POST"): Promise<T> {
    try {
      return await api<T>(path, { method, ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) throw new ConflictError(error.message);
      throw error;
    } finally { await this.refresh(); }
  }
  async createSource(input: CreateSourceInput) {
    const result = await this.mutate<BackendSource>("/sources", { account_id: this.state.account.id,
      kind: sourceKinds[input.type], value: input.value,
      label: input.label, enabled: input.enabled, requested_limit: input.collection_size, requested_order: input.ordering });
    return sourceView(result);
  }
  async updateSource(id: string, patch: { enabled?: boolean }) { await this.mutate(`/sources/${id}`, patch, "PATCH"); }
  async updateSourceSchedule(id: string, enabled: boolean, localTime: string, expectedVersion: number) { await this.mutate(`/sources/${id}/schedule`, { enabled, local_time: localTime, expected_version: expectedVersion }, "PATCH"); }
  async collectCandidates(id: string) { await this.mutate(`/sources/${id}/collect`, { idempotency_key: crypto.randomUUID() }); return 0; }
  async enqueueFollows(targetIds: string[]): Promise<EnqueueResult> {
    if (!this.state.connection?.can_follow) throw new Error("Following is currently unavailable. This workspace supports read-only collection.");
    const result = await this.mutate<{ jobs: BackendJob[]; existing: BackendJob[]; rejected: { target_id: string; reason: string }[] }>("/jobs/bulk", {
      account_id: this.state.account.id, target_ids: targetIds, idempotency_key: crypto.randomUUID(),
    });
    return { jobs: result.jobs.map(job => jobView(job, this.state.account.mode, this.state.relationships)), excluded: result.rejected.length, skipped: result.existing.length };
  }
  async patchRelationship(id: string, patch: Partial<Pick<Relationship, "protected" | "excluded">>, expectedVersion: number) {
    return this.mutate<Relationship>(`/relationships/${id}`, { ...patch, expected_version: expectedVersion }, "PATCH");
  }
  async cancelJob(id: string) { await this.mutate(`/jobs/${id}/cancel`); }
  async setWorkflow(workflow: "acquisition" | "cleanup", enabled: boolean, expectedVersion: number) { await this.mutate(`/accounts/${this.state.account.id}/workflows`, { workflow, enabled, expected_version: expectedVersion, acknowledged: enabled }, "PATCH"); }
  async adoptRelationship(id: string, expectedVersion: number) { return this.mutate<Relationship>(`/relationships/${id}/adopt`, { expected_version: expectedVersion, acknowledged: true }); }
  async retryJob(id: string): Promise<void> { await this.mutate(`/jobs/${id}/retry`, { idempotency_key: crypto.randomUUID() }); }
  async checkOutcome(id: string): Promise<void> { await this.mutate(`/jobs/${id}/reconcile`, { idempotency_key: crypto.randomUUID() }); }
  async updateSettings(settings: Settings) {
    const { version, ...values } = settings;
    await this.mutate(`/accounts/${this.state.account.id}/settings`, { ...values, expected_version: version }, "PATCH");
  }
  async setPaused(paused: boolean) { await this.mutate(`/accounts/${this.state.account.id}/pause`, { paused }, "PATCH"); }
  async recheckCandidate(id: string) {
    const record = this.state.relationships.find(x => x.target_id === id);
    if (!record) throw new Error("Open this candidate's ledger page before requesting a state check.");
    await this.mutate(`/relationships/${record.id}/recheck`, { idempotency_key: crypto.randomUUID() });
  }
  async simulateRun(): Promise<number> { throw new Error("The backend worker advances the queue automatically while resumed."); }
  async simulateRestriction(): Promise<void> { throw new Error("Prototype tools are disabled."); }
  async simulateCooldown(): Promise<void> { throw new Error("Prototype tools are disabled."); }
  async setSessionState(): Promise<void> { throw new Error("Connect and recover the account with the backend command."); }
  setFixture() { void this.refresh(); }
}
