import type { components } from "@/lib/api.generated";

export type Mode = "mock" | "live";
export type RelationshipState = "unknown" | "none" | "following" | "requested";
export type JobState =
  | "queued"
  | "running"
  | "succeeded"
  | "simulated"
  | "skipped"
  | "failed"
  | "unknown"
  | "cancelled"
  | "blocked";
export type JobKind = components["schemas"]["JobResponse"]["kind"];
export type FixtureState = "normal" | "empty" | "loading" | "error" | "stale";
export type SessionState = "disconnected" | "connected" | "expired" | "needs_verification";

export interface ArchiveAccount {
  instagram_id?: string | null;
  workflow_version?: number;
  live_acquisition_enabled?: boolean;
  live_cleanup_enabled?: boolean;
  id: string;
  username: string;
  mode: Mode;
  paused: boolean;
  restricted: boolean;
  cooldown_until: string | null;
  pause_reason: string | null;
  session_state: SessionState;
}

export interface Candidate {
  liked_posts?: number;
  shared_profiles?: number;
  target_id: string;
  username: string;
  source_ids: string[];
  first_seen_at: string;
  eligible: boolean;
  reason: string | null;
  category:
    | "eligible"
    | "previously_followed"
    | "already_following"
    | "requested"
    | "protected"
    | "excluded"
    | "unknown";
}

export interface Relationship {
  cleanup?: import("@/lib/api.generated").components["schemas"]["CleanupAssessment"];
  follows_back?: boolean | null;
  follows_back_observed_at?: string | null;
  unfollow_confirmed_at?: string | null;
  adopted_at?: string | null;
  id: string;
  account_id: string;
  target_id: string;
  username: string;
  observed_state: RelationshipState;
  managed: boolean;
  protected: boolean;
  excluded: boolean;
  ever_followed: boolean;
  version: number;
  follow_requested_at: string | null;
  follow_confirmed_at: string | null;
  unfollow_due_at: string | null;
  last_checked_at: string | null;
  source_ids: string[];
}

export interface Job {
  dispatch_started_at?: string | null;
  id: string;
  account_id: string;
  target_id: string | null;
  kind: JobKind;
  state: JobState;
  scheduled_at: string;
  error: string | null;
  username: string;
  source_id: string | null;
  attempt_count: number;
  mode: Mode;
  completed_at: string | null;
  request_id: string;
}

export interface Source {
  schedule_enabled?: boolean;
  schedule_local_time?: string;
  schedule_version?: number;
  collection_note?: string | null;
  id: string;
  label: string;
  type: "profile" | "mutual" | "post";
  value: string;
  enabled: boolean;
  collection_size: number;
  ordering: "default" | "newest" | "oldest";
  last_collected_at: string | null;
  discovered_count: number;
  overlap_count: number;
  result: "idle" | "queued" | "running" | "completed" | "partial" | "error";
  art: number;
  category: string;
}

export type CreateSourceInput = Pick<
  Source,
  "label" | "type" | "value" | "enabled" | "collection_size" | "ordering"
>;

export interface Activity {
  id: string;
  target: string;
  operation: string;
  result: string;
  reason: string;
  category: "action" | "configuration" | "error";
  created_at: string;
  mode: Mode;
  job_id: string | null;
  request_id: string;
}

export interface Settings {
  ranked_follow_mode?: "review" | "eligible" | "likers" | "overlap";
  follow_pacing?: "daily" | "hourly";
  follows_per_hour_min?: number;
  follows_per_hour_max?: number;
  jobs_per_run?: number;
  version?: number;
  follows_per_day: number | null;
  unfollows_per_day: number;
  request_ceiling: number | null;
  collection_batch_size: number;
  window_start: string;
  window_end: string;
  retention_days: number;
  pending_request_policy: "manual_review" | "review_after_retention";
  timezone: string;
}

export interface ChartDay {
  date: string;
  follows: number;
  follow_backs: number;
}

export interface Overview {
  operations?: import("@/lib/api.generated").components["schemas"]["OperationsResponse"];
  job_counts?: Record<string, number>;
  requests_used?: number;
  candidates_available: number;
  queued_follows: number;
  unfollows_due: number;
  protected_accounts: number;
  follows_used: number;
  unfollows_used: number;
  run: { processed: number; skipped: number; failed: number; review: number };
  chart: ChartDay[];
  next_job: Job | null;
}

export interface Snapshot {
  accounts?: Pick<ArchiveAccount, "id" | "username" | "mode" | "instagram_id">[];
  connection?: import("@/lib/api.generated").components["schemas"]["ConnectionResponse"];
  pagination?: Record<string, number>;
  worker_status?: string;
  worker_last_heartbeat?: string | null;
  account: ArchiveAccount;
  sources: Source[];
  candidates: Candidate[];
  relationships: Relationship[];
  jobs: Job[];
  activity: Activity[];
  settings: Settings;
  last_synced_at: string;
  fixture: FixtureState;
  overview: Overview;
}

export interface EnqueueResult {
  jobs: Job[];
  excluded: number;
  skipped: number;
}

export interface ArchiveProvider {
  updateSourceSchedule(id: string, enabled: boolean, localTime: string, expectedVersion: number): Promise<void>;
  setWorkflow(workflow: "acquisition" | "cleanup", enabled: boolean, expectedVersion: number): Promise<void>;
  adoptRelationship(id: string, expectedVersion: number): Promise<Relationship>;
  subscribe: (listener: () => void) => () => void;
  getSnapshot: () => Snapshot;
  getOverview(): Promise<Overview>;
  listSources(): Promise<Source[]>;
  createSource(input: CreateSourceInput): Promise<Source>;
  updateSource(id: string, patch: Partial<Pick<Source, "enabled">>): Promise<void>;
  collectCandidates(id: string): Promise<number>;
  listCandidates(): Promise<Candidate[]>;
  enqueueFollows(targetIds: string[]): Promise<EnqueueResult>;
  listRelationships(): Promise<Relationship[]>;
  patchRelationship(
    id: string,
    patch: Partial<Pick<Relationship, "protected" | "excluded">>,
    expectedVersion: number,
  ): Promise<Relationship>;
  listJobs(): Promise<Job[]>;
  cancelJob(id: string): Promise<void>;
  retryJob(id: string): Promise<void>;
  checkOutcome(id: string): Promise<void>;
  listActivity(): Promise<Activity[]>;
  getSettings(): Promise<Settings>;
  updateSettings(settings: Settings): Promise<void>;
  setPaused(paused: boolean, acknowledged?: boolean): Promise<void>;
  simulateRun(): Promise<number>;
  simulateRestriction(): Promise<void>;
  simulateCooldown(): Promise<void>;
  recheckCandidate(id: string): Promise<void>;
  setSessionState(state: SessionState): Promise<void>;
  setFixture(state: FixtureState): void;
}
