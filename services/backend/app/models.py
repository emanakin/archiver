import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def now():
    return datetime.now(UTC)


def uid():
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class Account(Base):
    __tablename__ = "accounts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    username: Mapped[str] = mapped_column(String(64))
    instagram_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    paused: Mapped[bool] = mapped_column(Boolean, default=True)
    mode: Mapped[str] = mapped_column(String(16), default="mock")
    session_state: Mapped[str] = mapped_column(String(24), default="disconnected")
    restricted: Mapped[bool] = mapped_column(Boolean, default=False)
    cooldown_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    automatic_pause_reason: Mapped[str | None] = mapped_column(String(100))
    live_acquisition_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    live_cleanup_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    workflow_version: Mapped[int] = mapped_column(Integer, default=1)
    read_only_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Source(Base):
    __tablename__ = "sources"
    __table_args__ = (UniqueConstraint("account_id", "kind", "value"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    kind: Mapped[str] = mapped_column(String(24))
    value: Mapped[str] = mapped_column(String(512))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    label: Mapped[str] = mapped_column(String(100), default="")
    requested_limit: Mapped[int] = mapped_column(Integer, default=20)
    requested_order: Mapped[str] = mapped_column(String(16), default="default")
    last_collected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    collection_state: Mapped[str] = mapped_column(String(16), default="idle")
    collection_cursor: Mapped[str | None] = mapped_column(String(200))
    collection_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    resolved_instagram_id: Mapped[str | None] = mapped_column(String(64))
    collection_note: Mapped[str | None] = mapped_column(String(200))
    schedule_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    schedule_local_time: Mapped[str] = mapped_column(String(5), default="09:00")
    schedule_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Target(Base):
    __tablename__ = "targets"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    username: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Relationship(Base):
    __tablename__ = "relationships"
    __table_args__ = (UniqueConstraint("account_id", "target_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    target_id: Mapped[str] = mapped_column(ForeignKey("targets.id"))
    observed_state: Mapped[str] = mapped_column(String(24), default="unknown")
    managed: Mapped[bool] = mapped_column(Boolean, default=False)
    protected: Mapped[bool] = mapped_column(Boolean, default=False)
    excluded: Mapped[bool] = mapped_column(Boolean, default=False)
    ever_followed: Mapped[bool] = mapped_column(Boolean, default=False)
    version: Mapped[int] = mapped_column(Integer, default=1)
    follow_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    follow_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    unfollow_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    unfollow_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    follows_back: Mapped[bool | None] = mapped_column(Boolean)
    follows_back_observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    adopted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Discovery(Base):
    __tablename__ = "discoveries"
    __table_args__ = (
        UniqueConstraint("source_id", "target_id"),
        Index("ix_discoveries_target_id", "target_id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"))
    target_id: Mapped[str] = mapped_column(ForeignKey("targets.id"))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("account_id", "idempotency_key"),
        Index("ix_jobs_due", "state", "scheduled_at"),
        Index("ix_jobs_account_state", "account_id", "state", "scheduled_at"),
        Index("ix_jobs_account_target_kind", "account_id", "target_id", "kind", "state"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    target_id: Mapped[str | None] = mapped_column(ForeignKey("targets.id"))
    kind: Mapped[str] = mapped_column(String(32))
    state: Mapped[str] = mapped_column(String(24), default="queued")
    idempotency_key: Mapped[str] = mapped_column(String(200))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    request_fingerprint: Mapped[str | None] = mapped_column(String(64))
    claim_owner: Mapped[str | None] = mapped_column(String(36))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dispatch_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    priority: Mapped[int] = mapped_column(Integer, default=0)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Action(Base):
    __tablename__ = "actions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), unique=True)
    kind: Mapped[str] = mapped_column(String(32))
    outcome: Mapped[str] = mapped_column(String(32))
    simulated: Mapped[bool] = mapped_column(Boolean, default=True)
    detail: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    event: Mapped[str] = mapped_column(String(64))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class AccountSettings(Base):
    __tablename__ = "account_settings"
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    values: Mapped[dict] = mapped_column(JSON, default=dict)


class FollowIntent(Base):
    __tablename__ = "follow_intents"
    __table_args__ = (UniqueConstraint("account_id", "target_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    target_id: Mapped[str] = mapped_column(ForeignKey("targets.id"))
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"))


class WorkerHeartbeat(Base):
    __tablename__ = "worker_heartbeats"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class IntentReceipt(Base):
    __tablename__ = "intent_receipts"
    __table_args__ = (UniqueConstraint("account_id", "idempotency_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    idempotency_key: Mapped[str] = mapped_column(String(200))
    operation: Mapped[str] = mapped_column(String(32))
    fingerprint: Mapped[str] = mapped_column(String(64))
    job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id"))
    result: Mapped[dict | None] = mapped_column(JSON)


class ActionAttempt(Base):
    __tablename__ = "action_attempts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), index=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    owner: Mapped[str] = mapped_column(String(36))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    dispatch_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outcome: Mapped[str] = mapped_column(String(32), default="claimed")
    error_code: Mapped[str | None] = mapped_column(String(64))
    request_count: Mapped[int] = mapped_column(Integer, default=0)


class AttemptEvent(Base):
    __tablename__ = "attempt_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    attempt_id: Mapped[str] = mapped_column(ForeignKey("action_attempts.id"), index=True)
    event: Mapped[str] = mapped_column(String(40))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class BudgetUsage(Base):
    __tablename__ = "budget_usage"
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), primary_key=True)
    local_day: Mapped[str] = mapped_column(String(10), primary_key=True)
    category: Mapped[str] = mapped_column(String(16), primary_key=True)
    consumed: Mapped[int] = mapped_column(Integer, default=0)
    reserved: Mapped[int] = mapped_column(Integer, default=0)


class BudgetReservation(Base):
    __tablename__ = "budget_reservations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    attempt_id: Mapped[str] = mapped_column(ForeignKey("action_attempts.id"), index=True)
    local_day: Mapped[str] = mapped_column(String(10))
    category: Mapped[str] = mapped_column(String(16))
    remaining: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    released: Mapped[bool] = mapped_column(Boolean, default=False)


class AccountSession(Base):
    __tablename__ = "account_sessions"
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), primary_key=True)
    revision: Mapped[str] = mapped_column(String(36))
    pending_revision: Mapped[str | None] = mapped_column(String(36))
    library_version: Mapped[str] = mapped_column(String(32))
    last_validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    capabilities: Mapped[dict] = mapped_column(JSON, default=dict)
    import_cursor: Mapped[str | None] = mapped_column(String(200))
    import_complete: Mapped[bool] = mapped_column(Boolean, default=False)


class UsernameHistory(Base):
    __tablename__ = "username_history"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    target_id: Mapped[str] = mapped_column(ForeignKey("targets.id"), index=True)
    username: Mapped[str] = mapped_column(String(64))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class OnboardingKey(Base):
    __tablename__ = "onboarding_keys"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    public_key: Mapped[str] = mapped_column(String(100))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class AccountConnection(Base):
    __tablename__ = "account_connections"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    key_id: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(200), unique=True)
    state: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    password_envelope: Mapped[dict | None] = mapped_column(JSON)
    code_envelope: Mapped[dict | None] = mapped_column(JSON)
    challenge_id: Mapped[str | None] = mapped_column(String(36))
    challenge_kind: Mapped[str | None] = mapped_column(String(24))
    code_submitted: Mapped[bool] = mapped_column(Boolean, default=False)
    recovery_acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)
    max_requests: Mapped[int] = mapped_column(Integer, default=12)
    owner: Mapped[str | None] = mapped_column(String(36))
    job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id"))
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class InsightCheck(Base):
    __tablename__ = "insight_checks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), unique=True)
    kind: Mapped[str] = mapped_column(String(24))
    target: Mapped[str] = mapped_column(String(512))
    requested_limit: Mapped[int] = mapped_column(Integer, default=5)
    cursor: Mapped[str | None] = mapped_column(String(200))
    next_cursor: Mapped[str | None] = mapped_column(String(200))
    complete: Mapped[bool | None] = mapped_column(Boolean)
    profile: Mapped[dict | None] = mapped_column(JSON)
    relationship: Mapped[dict | None] = mapped_column(JSON)
    items: Mapped[list] = mapped_column(JSON, default=list)
    note: Mapped[str | None] = mapped_column(String(500))
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class InsightWatch(Base):
    __tablename__ = "insight_watches"
    __table_args__ = (UniqueConstraint("account_id", "target"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    target: Mapped[str] = mapped_column(String(512))
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    interval_minutes: Mapped[int] = mapped_column(Integer, default=60)
    sample_size: Mapped[int] = mapped_column(Integer, default=5)
    version: Mapped[int] = mapped_column(Integer, default=1)
    next_due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    first_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class LikerObservation(Base):
    __tablename__ = "liker_observations"
    watch_id: Mapped[str] = mapped_column(ForeignKey("insight_watches.id"), primary_key=True)
    target_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    username: Mapped[str] = mapped_column(String(64))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    baseline: Mapped[bool] = mapped_column(Boolean)


class RelationshipTest(Base):
    __tablename__ = "relationship_tests"
    __table_args__ = (UniqueConstraint("account_id", "target_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    target_id: Mapped[str] = mapped_column(ForeignKey("targets.id"))
    username: Mapped[str] = mapped_column(String(64))
    before_check_id: Mapped[str] = mapped_column(ForeignKey("insight_checks.id"))
    state: Mapped[str] = mapped_column(String(24), default="prepared")
    follow_job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id"))
    unfollow_job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id"))
    error_code: Mapped[str | None] = mapped_column(String(64))
    authorized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ContentAsset(Base):
    __tablename__ = "content_assets"
    __table_args__ = (UniqueConstraint("account_id", "sha256"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    sha256: Mapped[str] = mapped_column(String(64))
    storage_backend: Mapped[str] = mapped_column(
        String(12), default="local", server_default="local"
    )
    mime: Mapped[str] = mapped_column(String(32))
    byte_size: Mapped[int] = mapped_column(Integer)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ContentDraft(Base):
    __tablename__ = "content_drafts"
    __table_args__ = (UniqueConstraint("source_draft_id", name="uq_content_drafts_source_draft"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey("content_assets.id"))
    photo_assets: Mapped[list | None] = mapped_column(JSON)
    photo_order: Mapped[list | None] = mapped_column(JSON)
    source_draft_id: Mapped[str | None] = mapped_column(ForeignKey("content_drafts.id"))
    kind: Mapped[str] = mapped_column(String(24), default="feed_photo")
    caption: Mapped[str] = mapped_column(String(2200), default="")
    music_canonical_id: Mapped[str | None] = mapped_column(String(32))
    playlist_music: Mapped[dict | None] = mapped_column(JSON)
    source_music: Mapped[dict | None] = mapped_column(JSON)
    reel_source: Mapped[dict | None] = mapped_column(JSON)
    caption_review: Mapped[dict | None] = mapped_column(JSON)
    fit_mode: Mapped[str] = mapped_column(String(12), default="native")
    rotation: Mapped[int] = mapped_column(Integer, default=0)
    overlay_text: Mapped[str] = mapped_column(String(180), default="")
    text_position: Mapped[str] = mapped_column(String(12), default="bottom")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(24), default="draft")
    media_id: Mapped[str | None] = mapped_column(String(64))
    publication_job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    discarded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DmInbox(Base):
    __tablename__ = "dm_inboxes"
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), primary_key=True)
    sender_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    sender_username: Mapped[str] = mapped_column(String(64))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    thread_id: Mapped[str | None] = mapped_column(String(64))
    cursor: Mapped[str | None] = mapped_column(String(512))
    scan_head_id: Mapped[str | None] = mapped_column(String(64))
    pending_caption: Mapped[dict | None] = mapped_column(JSON)
    direct_publish_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    watermark: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    last_polled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DmSubmission(Base):
    __tablename__ = "dm_submissions"
    __table_args__ = (UniqueConstraint("account_id", "thread_id", "message_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    sender_id: Mapped[str] = mapped_column(String(64))
    thread_id: Mapped[str] = mapped_column(String(64))
    message_id: Mapped[str] = mapped_column(String(64))
    media_ref: Mapped[str | None] = mapped_column(String(128))
    caption_command: Mapped[dict | None] = mapped_column(JSON)
    draft_commands: Mapped[dict | None] = mapped_column(JSON)
    draft_id: Mapped[str | None] = mapped_column(ForeignKey("content_drafts.id"))
    job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class PlaylistTrack(Base):
    __tablename__ = "playlist_tracks"
    playlist_id: Mapped[str] = mapped_column(String(22), primary_key=True)
    spotify_id: Mapped[str] = mapped_column(String(22), primary_key=True)
    title: Mapped[str] = mapped_column(String(250))
    artist: Mapped[str] = mapped_column(String(250))


class PlaylistSync(Base):
    __tablename__ = "playlist_sync"

    playlist_id: Mapped[str] = mapped_column(String(22), primary_key=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    state: Mapped[str] = mapped_column(String(16))


class SavedAudio(Base):
    __tablename__ = "saved_audio"

    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), primary_key=True)
    audio_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    title: Mapped[str] = mapped_column(String(250))
    artist: Mapped[str] = mapped_column(String(250))
    state: Mapped[str] = mapped_column(String(24), default="queued")
    feed_music: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PlaylistSelection(Base):
    __tablename__ = "playlist_selections"
    __table_args__ = (
        UniqueConstraint("account_id", "spotify_id"),
        UniqueConstraint("account_id", "song_key"),
        UniqueConstraint("account_id", "catalog_id"),
        UniqueConstraint("account_id", "audio_asset_id", name="uq_playlist_selection_asset"),
        UniqueConstraint("account_id", "audio_cluster_id", name="uq_playlist_selection_cluster"),
    )
    draft_id: Mapped[str] = mapped_column(ForeignKey("content_drafts.id"), primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    spotify_id: Mapped[str] = mapped_column(String(22))
    song_key: Mapped[str] = mapped_column(String(64))
    catalog_id: Mapped[str | None] = mapped_column(String(32))
    audio_asset_id: Mapped[str | None] = mapped_column(String(32))
    audio_cluster_id: Mapped[str | None] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(250))
    artist: Mapped[str] = mapped_column(String(250))
    selected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
