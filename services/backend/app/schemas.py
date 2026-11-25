import re
from datetime import datetime, time
from typing import Literal
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


SourceKind = Literal["profile_followers", "profile_mutuals", "post_likers"]
MUTUAL_BATCH_LIMIT = 3


class SourceCreate(Strict):
    account_id: str
    kind: SourceKind
    value: str = Field(min_length=1, max_length=512)
    label: str = Field(default="", max_length=100)
    enabled: bool = True
    requested_limit: int = Field(default=20, ge=1, le=100)
    requested_order: Literal["default", "newest", "oldest"] = "default"

    @model_validator(mode="after")
    def normalize(self):
        value = self.value.strip()
        if self.kind != "post_likers":
            value = value.removeprefix("@").lower()
            if not re.fullmatch(r"[a-z0-9._]{1,30}", value):
                raise ValueError("SOURCE_INVALID: enter an Instagram handle")
            if self.kind == "profile_mutuals" and (
                self.requested_order != "default" or self.requested_limit > MUTUAL_BATCH_LIMIT
            ):
                raise ValueError("Mutual collection uses default order and a batch of 1–3")
        else:
            url = urlparse(value)
            if (
                url.scheme not in {"http", "https"}
                or url.hostname not in {"instagram.com", "www.instagram.com"}
                or url.username
                or url.password
                or url.port is not None
                or not re.fullmatch(r"/(p|reel|reels)/[A-Za-z0-9_-]+/?", url.path)
            ):
                raise ValueError("SOURCE_INVALID: enter an Instagram post or Reel URL")
            path = url.path.replace("/reels/", "/reel/").rstrip("/")
            value = f"https://www.instagram.com{path}/"
        self.value = value
        self.label = self.label.strip() or value
        return self


class SourcePatch(Strict):
    enabled: bool


class RelationshipPatch(Strict):
    expected_version: int = Field(ge=1)
    protected: bool | None = None
    excluded: bool | None = None


class PausePatch(Strict):
    paused: bool


class Intent(Strict):
    idempotency_key: str = Field(min_length=8, max_length=200)


class JobCreate(Intent):
    account_id: str
    target_id: str
    kind: Literal["follow", "unfollow", "reconcile"]
    scheduled_at: datetime | None = None

    @field_validator("scheduled_at")
    @classmethod
    def aware(cls, value):
        if value is not None and value.utcoffset() is None:
            raise ValueError("scheduled_at must include a timezone")
        return value


class BulkJobs(Intent):
    account_id: str
    target_ids: list[str] = Field(min_length=1, max_length=100)


class MusicPlaylist(Strict):
    playlist_id: str = Field(pattern=r"^[A-Za-z0-9]{22}$")
    label: str = Field(min_length=1, max_length=80)
    enabled: bool = True


class SettingsValues(Strict):
    ranked_follow_mode: Literal["review", "eligible", "likers", "overlap"] = "review"
    follows_per_hour_min: int = Field(default=75, ge=1, le=1000)
    follows_per_hour_max: int = Field(default=100, ge=1, le=1000)
    follow_pacing: Literal["daily", "hourly"] = "daily"

    @model_validator(mode="after")
    def hourly_range(self):
        if self.follows_per_hour_min > self.follows_per_hour_max:
            raise ValueError("Hourly minimum must not exceed hourly maximum")
        if self.follow_pacing == "daily" and self.follows_per_day is None:
            raise ValueError("Unlimited daily follows require hourly pacing")
        return self

    music_library: list[MusicPlaylist] | None = Field(default=None, max_length=30)

    @field_validator("music_library")
    @classmethod
    def distinct_playlists(cls, value):
        if value and len({p.playlist_id for p in value}) != len(value):
            raise ValueError("This playlist is already in the library")
        return value

    follows_per_day: int | None = Field(default=0, ge=0, le=500)
    unfollows_per_day: int = Field(default=0, ge=0, le=500)
    request_ceiling: int | None = Field(default=0, ge=0, le=10000)
    collection_batch_size: int = Field(default=20, ge=1, le=100)
    jobs_per_run: int = Field(default=10, ge=1, le=100)
    window_start: str = "00:00"
    window_end: str = "00:00"
    retention_days: int = Field(default=15, ge=1, le=365)
    pending_request_policy: Literal["manual_review", "review_after_retention"] = "manual_review"
    timezone: str = "America/Toronto"

    @field_validator("window_start", "window_end")
    @classmethod
    def clock(cls, value):
        if not re.fullmatch(r"\d{2}:\d{2}", value):
            raise ValueError("Use HH:MM")
        time.fromisoformat(value)
        return value

    @field_validator("timezone")
    @classmethod
    def zone(cls, value):
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError:
            raise ValueError("Unknown IANA timezone") from None
        return value


class SettingsResponse(SettingsValues):
    version: int


class SettingsPatch(SettingsValues):
    expected_version: int = Field(ge=1)


class FollowTelemetry(Strict):
    window_start: str
    observed_at: str
    follow_dispatches: int
    follow_successes: int
    requests: int
    errors: int
    dispatch_times: list[str]
    interval_seconds: list[float]


class ORMResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class AccountResponse(ORMResponse):
    id: str
    username: str
    instagram_id: str | None
    paused: bool
    mode: Literal["mock", "live"]
    created_at: datetime
    session_state: Literal["disconnected", "connected", "expired", "needs_verification"] = (
        "disconnected"
    )
    restricted: bool = False
    cooldown_until: datetime | None = None
    automatic_pause_reason: str | None = None
    live_acquisition_enabled: bool = False
    live_cleanup_enabled: bool = False
    workflow_version: int = 1
    read_only_verified_at: datetime | None = None


class SourceResponse(ORMResponse):
    id: str
    account_id: str
    kind: SourceKind
    value: str
    label: str
    enabled: bool
    requested_limit: int
    requested_order: Literal["default", "newest", "oldest"]
    last_collected_at: datetime | None
    collection_state: Literal["idle", "queued", "running", "completed", "partial", "error"]
    collection_cursor: str | None
    collection_complete: bool
    discovered_count: int = 0
    overlap_count: int = 0
    collection_note: str | None = None
    schedule_enabled: bool = False
    schedule_local_time: str = "09:00"
    schedule_version: int = 1


class SourceSchedulePatch(Strict):
    enabled: bool
    local_time: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    expected_version: int = Field(ge=1)


class CleanupAssessment(Strict):
    state: Literal[
        "protected",
        "excluded",
        "outcome_unknown",
        "manual_review",
        "not_following",
        "unmanaged",
        "needs_observation",
        "follows_back",
        "retention_unknown",
        "retention_pending",
        "due_for_check",
    ]
    reason: str
    retention_due_at: datetime | None


class RelationshipResponse(ORMResponse):
    id: str
    account_id: str
    target_id: str
    username: str = ""
    observed_state: Literal["unknown", "none", "requested", "following"]
    managed: bool
    protected: bool
    excluded: bool
    ever_followed: bool
    version: int
    follow_requested_at: datetime | None
    follow_confirmed_at: datetime | None
    unfollow_due_at: datetime | None
    unfollow_confirmed_at: datetime | None
    last_checked_at: datetime | None
    source_ids: list[str] = Field(default_factory=list)
    follows_back: bool | None = None
    follows_back_observed_at: datetime | None = None
    adopted_at: datetime | None = None
    cleanup: CleanupAssessment


class JobResponse(ORMResponse):
    id: str
    account_id: str
    target_id: str | None
    kind: Literal[
        "follow",
        "unfollow",
        "reconcile",
        "discover",
        "connect",
        "verify",
        "import_following",
        "insight",
        "publish_photo",
        "publish_story",
        "publish_carousel",
        "publish_reel",
        "compose_caption",
        "edit_caption",
        "delete_story",
        "delete_post",
        "reconcile_content",
        "dm_poll",
        "dm_import",
        "dm_audio",
        "dm_receipt",
        "music_diagnostic",
        "import_source_photo",
    ]
    state: Literal[
        "queued",
        "running",
        "succeeded",
        "simulated",
        "skipped",
        "failed",
        "unknown",
        "cancelled",
        "blocked",
    ]
    scheduled_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    error: str | None
    idempotency_key: str
    payload: dict
    attempt_count: int = 0
    dispatch_started_at: datetime | None = None
    lease_expires_at: datetime | None = None


class AttemptEventResponse(ORMResponse):
    id: str
    event: str
    detail: dict
    created_at: datetime


class AttemptResponse(ORMResponse):
    id: str
    job_id: str
    account_id: str
    started_at: datetime
    dispatch_started_at: datetime | None
    completed_at: datetime | None
    outcome: str
    error_code: str | None
    request_count: int
    events: list[AttemptEventResponse] = Field(default_factory=list)


class CandidateResponse(Strict):
    target_id: str
    username: str
    liked_posts: int
    shared_profiles: int
    source_ids: list[str]
    first_seen_at: datetime
    eligible: bool
    reason: str | None
    category: Literal[
        "eligible",
        "previously_followed",
        "already_following",
        "requested",
        "protected",
        "excluded",
        "unknown",
    ]


class ActivityResponse(Strict):
    id: str
    target: str
    operation: str
    result: str
    reason: str
    category: Literal["action", "configuration", "error"]
    created_at: datetime
    mode: Literal["mock", "live"]
    job_id: str | None
    request_id: str


class ActionResponse(ORMResponse):
    id: str
    job_id: str
    kind: str
    outcome: str
    simulated: bool
    detail: str
    created_at: datetime


class WorkerResponse(Strict):
    status: Literal["online", "stale", "unavailable"]
    last_heartbeat: datetime | None
    simulated: bool


class ConnectionResponse(Strict):
    session_state: str
    transport_mode: Literal["mock", "read_only", "live"]
    last_validated_at: datetime | None
    library_version: str | None
    capabilities: dict[str, bool]
    import_complete: bool
    can_collect: bool
    can_follow: bool
    can_unfollow: bool


class WorkflowPatch(Strict):
    workflow: Literal["acquisition", "cleanup"]
    enabled: bool
    expected_version: int = Field(ge=1)
    acknowledged: bool = False


class AdoptionInput(Strict):
    expected_version: int = Field(ge=1)
    acknowledged: bool


class OperationsResponse(Strict):
    oldest_due_at: datetime | None
    oldest_due_age_seconds: int | None
    last_relationship_observation: datetime | None
    last_collection_at: datetime | None
    unknown_writes: int


class OverviewResponse(Strict):
    account: AccountResponse
    job_counts: dict[str, int]
    mode: str
    live_metrics: None = None
    candidates_available: int
    queued_follows: int
    unfollows_due: int
    protected_accounts: int
    follows_used: int = 0
    unfollows_used: int = 0
    requests_used: int = 0
    run: dict[str, int]
    chart: list = Field(default_factory=list)
    next_job: JobResponse | None
    operations: OperationsResponse


class Page[T](BaseModel):
    items: list[T]
    total: int
    limit: int
    offset: int


class BulkResult(Strict):
    jobs: list[JobResponse]
    existing: list[JobResponse]
    rejected: list[dict[str, str]]
