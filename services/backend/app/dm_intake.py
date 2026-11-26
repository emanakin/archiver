"""Opt-in direct message reads with durable checkpoints and draft provenance."""

import random
import uuid
from datetime import datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .caption_writer import (
    add_caption_tags,
    archive_caption,
    caption_image,
    has_editorial_context,
    image_sample,
    style_examples,
    write_caption,
)
from .catalog_music import source_feed_track
from .config import settings
from .content_drafts import is_photo_order
from .content_images import validate_image
from .content_storage import persist_asset, upload_backend
from .content_video import validate_video
from .dashboard import required
from .db import get_db
from .dm_reader import SharedReel, download_reel, identity, read_page, read_shared_photos
from .execution_errors import BudgetExhausted, Inaccessible, OperationBlocked, OwnershipLost
from .models import (
    Account,
    AuditEvent,
    BudgetUsage,
    ContentDraft,
    DmInbox,
    DmSubmission,
    InsightCheck,
    Job,
    PlaylistSelection,
    now,
)
from .music_context import named_song, named_track
from .saved_audio import audio_candidate, mark_used, record_audio, spent_audio_assets, verify_audio
from .schemas import JobResponse, ORMResponse, Page, Strict
from .spotify_playlist import match_track, music_library, playlist_candidates, spotify_query
from .workspace_preferences import preferences

router = APIRouter(prefix="/api/v1")
DM_KINDS = {"dm_poll", "dm_import", "dm_audio", "dm_replacement_music"}
DM_REQUEST_RESERVATION = 2


class InboxSettings(Strict):
    sender_account_id: str
    enabled: bool


class InboxResponse(Strict):
    sender_id: str
    sender_username: str
    enabled: bool
    last_polled_at: datetime | None
    has_older: bool
    direct_publish: bool
    poll_interval_seconds: int
    feed_autopost_default: bool
    local_captions: bool
    poll_state: Literal[
        "manual",
        "paused",
        "scheduled",
        "queued",
        "running",
        "backoff",
        "needs_attention",
        "needs_conversation",
    ] = "manual"
    next_poll_at: datetime | None = None
    last_poll_error: str | None = None
    consecutive_poll_failures: int = 0


class CaptionStage(Strict):
    state: str
    model: str | None = None
    duration_ms: int | None = None
    reason: str | None = None


class CaptionRun(Strict):
    style_references: int
    has_source_caption: bool
    stages: dict[str, CaptionStage]


class MusicCheck(Strict):
    spotify_id: str | None = None
    audio_id: str | None = None
    title: str
    artist: str
    state: str


class SubmissionResponse(ORMResponse):
    id: str
    sender_id: str
    media_ref: str | None
    draft_id: str | None
    job_id: str | None
    created_at: datetime
    state: str
    error: str | None
    caption_status: str | None
    caption_run: CaptionRun | None = None
    command_status: str | None
    publication_hold: str | None = None
    music_checks: list[MusicCheck] = []
    skipped_videos: int = 0
    reel_url: str | None = None
    reel_caption: str | None = None
    reel_import_note: str | None = None


def inbox_view(inbox, db=None):
    config = preferences(db, inbox.account_id, settings()) if db else settings()
    enabled_defaults = config.dm_defaults_apply(inbox.account_id)
    return InboxResponse(
        sender_id=inbox.sender_id,
        sender_username=inbox.sender_username,
        enabled=inbox.enabled,
        last_polled_at=inbox.last_polled_at,
        has_older=bool(inbox.cursor),
        direct_publish=inbox.direct_publish_from is not None,
        poll_interval_seconds=config.dm_poll_interval_seconds,
        feed_autopost_default=enabled_defaults,
        local_captions=enabled_defaults and bool(config.dm_caption_model),
        **(poll_status(db, inbox, now(), config.dm_poll_interval_seconds) if db else {}),
    )


def sender_inbox(db, account_id, sender_id=None):
    if sender_id:
        return db.get(DmInbox, (account_id, sender_id))
    return db.scalar(
        select(DmInbox)
        .where(DmInbox.account_id == account_id)
        .order_by(DmInbox.created_at, DmInbox.sender_id)
        .limit(1)
    )


@router.get("/accounts/{account_id}/dm-inboxes", response_model=list[InboxResponse])
def list_inboxes(account_id: str, db: Session = Depends(get_db)):
    required(db, Account, account_id)
    return [
        inbox_view(inbox, db)
        for inbox in db.scalars(
            select(DmInbox).where(DmInbox.account_id == account_id).order_by(DmInbox.created_at)
        )
    ]


@router.get("/accounts/{account_id}/dm-inbox", response_model=InboxResponse | None)
def get_inbox(account_id: str, db: Session = Depends(get_db), sender_id: str | None = None):
    required(db, Account, account_id)
    inbox = sender_inbox(db, account_id, sender_id)
    return inbox_view(inbox, db) if inbox else None


@router.put("/accounts/{account_id}/dm-inbox", response_model=InboxResponse)
def configure_inbox(account_id: str, body: InboxSettings, db: Session = Depends(get_db)):
    account = db.scalar(select(Account).where(Account.id == account_id).with_for_update())
    sender = required(db, Account, body.sender_account_id)
    if (
        not account
        or account.mode != "live"
        or sender.mode != "live"
        or not sender.instagram_id
        or not sender.read_only_verified_at
    ):
        raise HTTPException(409, "Choose a verified Instagram account as the sender")
    return save_sender(db, account_id, sender.instagram_id, sender.username, body.enabled)


def save_sender(db, account_id, sender_id, username, enabled):
    inbox = sender_inbox(db, account_id, sender_id)
    if not inbox:
        inbox = DmInbox(account_id=account_id, sender_id=sender_id)
        db.add(inbox)
    elif inbox.enabled != enabled:
        inbox.version += 1
        if not enabled:
            inbox.direct_publish_from = None
    inbox.sender_username, inbox.enabled = username, enabled
    db.add(
        AuditEvent(
            account_id=account_id,
            event="dm_inbox_configured",
            detail={"enabled": enabled, "sender_id": sender_id},
        )
    )
    db.commit()
    return inbox_view(inbox, db)


class VerifiedSender(Strict):
    check_id: str


@router.put("/accounts/{account_id}/dm-inbox/verified-sender", response_model=InboxResponse)
def approve_verified_sender(account_id: str, body: VerifiedSender, db: Session = Depends(get_db)):
    from .executor import aware

    account = db.scalar(select(Account).where(Account.id == account_id).with_for_update())
    check = db.get(InsightCheck, body.check_id)
    profile = check.profile or {} if check else {}
    username = str(profile.get("username", ""))
    sender_id = identity(profile.get("id"))
    if (
        not account
        or account.mode != "live"
        or not check
        or check.account_id != account_id
        or check.kind != "profile"
        or not check.observed_at
        or aware(check.observed_at) < now() - timedelta(hours=24)
        or username.casefold() != check.target
        or not sender_id
        or db.get(Job, check.job_id).state != "succeeded"
    ):
        raise HTTPException(409, "Verify the exact sender handle with a fresh profile observation")
    return save_sender(db, account_id, sender_id, username, True)


class SenderState(Strict):
    sender_id: str
    enabled: bool


@router.patch("/accounts/{account_id}/dm-inbox", response_model=InboxResponse)
def change_sender_state(account_id: str, body: SenderState, db: Session = Depends(get_db)):
    if not db.scalar(select(Account).where(Account.id == account_id).with_for_update()):
        raise HTTPException(404, "Account not found")
    inbox = sender_inbox(db, account_id, body.sender_id)
    if not inbox or not identity(body.sender_id):
        raise HTTPException(404, "Approved sender not found")
    return save_sender(db, account_id, inbox.sender_id, inbox.sender_username, body.enabled)


class DirectPublicationSettings(Strict):
    enabled: bool
    sender_id: str | None = None


@router.put("/accounts/{account_id}/dm-inbox/direct-publication", response_model=InboxResponse)
def set_direct_publication(
    account_id: str, body: DirectPublicationSettings, db: Session = Depends(get_db)
):
    account = db.scalar(select(Account).where(Account.id == account_id).with_for_update())
    inbox = sender_inbox(db, account_id, body.sender_id)
    if not account or not inbox or (body.enabled and not inbox.enabled):
        raise HTTPException(409, "Configure an approved sender first")
    if body.enabled:
        from .content_publication import ready

        ready(account)
    if body.enabled != bool(inbox.direct_publish_from):
        inbox.direct_publish_from = now() if body.enabled else None
        inbox.version += 1
        db.add(
            AuditEvent(
                account_id=account_id,
                event="dm_direct_publication_changed",
                detail={"enabled": body.enabled, "sender_id": inbox.sender_id},
            )
        )
        db.commit()
    return inbox_view(inbox, db)


def can_poll(db, account_id, sender_id=None):
    account = db.scalar(select(Account).where(Account.id == account_id).with_for_update())
    inbox = sender_inbox(db, account_id, sender_id)
    if (
        not account
        or account.mode != "live"
        or not account.instagram_id
        or account.session_state != "connected"
        or account.restricted
        or settings().adapter_mode == "mock"
    ):
        raise HTTPException(409, "Connect the archive account before reading its inbox")
    if not inbox or not inbox.enabled:
        raise HTTPException(409, "Enable the approved sender before reading messages")
    return inbox


def queue_job(db, inbox, kind, **payload):
    job = Job(
        account_id=inbox.account_id,
        kind=kind,
        idempotency_key="dm:" + str(uuid.uuid4()),
        payload={"inbox_version": inbox.version, "sender_id": inbox.sender_id, **payload},
    )
    db.add(job)
    db.flush()
    return job


def poll_jobs(db, inbox):
    sender = Job.payload["sender_id"].as_string()
    primary = sender_inbox(db, inbox.account_id)
    match = sender == inbox.sender_id
    if primary.sender_id == inbox.sender_id:
        match = or_(match, sender.is_(None))
    return select(Job).where(Job.account_id == inbox.account_id, Job.kind == "dm_poll", match)


def poll_status(db, inbox, at, interval_seconds):
    """Bound failed reads by sender; one inaccessible inbox must not flood the account lane."""
    from .executor import aware

    query = poll_jobs(db, inbox)
    recent = db.scalars(query.order_by(Job.created_at.desc(), Job.id.desc()).limit(3)).all()
    previous = recent[0] if recent else None
    status = {
        "poll_state": "manual",
        "next_poll_at": None,
        "last_poll_error": previous.error if previous else None,
        "consecutive_poll_failures": 0,
    }
    if not inbox.enabled:
        return {**status, "poll_state": "paused"}
    if previous and (
        previous.error == "DM_CONVERSATION_MISSING"
        or (previous.error == "SOURCE_INACCESSIBLE" and not inbox.thread_id)
    ):
        return {**status, "poll_state": "needs_conversation"}
    if not inbox.direct_publish_from:
        return status
    if previous and previous.state in {"queued", "running"}:
        return {**status, "poll_state": previous.state}
    if not previous or aware(previous.created_at) < aware(inbox.direct_publish_from):
        return {**status, "poll_state": "scheduled", "next_poll_at": at}
    if previous.state == "succeeded" or previous.error == "BUDGET_EXHAUSTED":
        return {
            **status,
            "poll_state": "scheduled",
            "next_poll_at": aware(previous.created_at) + timedelta(seconds=interval_seconds),
        }
    retryable = {"READ_FAILED"}
    failures = 0
    for job in recent:
        if aware(job.created_at) < aware(inbox.direct_publish_from):
            break
        if job.state in {"queued", "running", "succeeded"} or job.error not in retryable:
            break
        failures += 1
    status["consecutive_poll_failures"] = failures
    if previous.error not in retryable or failures >= 3:
        return {**status, "poll_state": "needs_attention"}
    delay = max(interval_seconds, 300 * 2 ** (failures - 1))
    return {
        **status,
        "poll_state": "backoff",
        "next_poll_at": aware(previous.completed_at or previous.created_at)
        + timedelta(seconds=delay),
    }


def materialize_direct_polls(engine, clock=now):
    from .dashboard import account_settings
    from .executor import aware
    from .timing import local_day, within_window

    at = clock()
    interval_seconds = settings().dm_poll_interval_seconds
    with Session(engine) as db:
        ids = list(
            db.execute(
                select(DmInbox.account_id, DmInbox.sender_id).where(
                    DmInbox.enabled, DmInbox.direct_publish_from.is_not(None)
                )
            )
        )
    for account_id, sender_id in ids:
        with Session(engine) as db, db.begin():
            account = db.scalar(
                select(Account).where(Account.id == account_id).with_for_update(skip_locked=True)
            )
            inbox = sender_inbox(db, account_id, sender_id)
            if (
                not account
                or account.paused
                or account.restricted
                or account.automatic_pause_reason
                or account.session_state != "connected"
                or (account.cooldown_until and aware(account.cooldown_until) > at)
                or not inbox
                or not inbox.enabled
                or not inbox.direct_publish_from
            ):
                continue
            config = account_settings(db, account_id)
            day = local_day(at, config)
            usage = db.get(BudgetUsage, (account_id, day, "requests"))
            ceiling = config["request_ceiling"]
            requests = (usage.consumed + usage.reserved if usage else 0) + DM_REQUEST_RESERVATION
            if not within_window(at, config) or (ceiling is not None and requests > ceiling):
                continue
            status = poll_status(db, inbox, at, interval_seconds)
            if status["next_poll_at"] is None or status["next_poll_at"] > at:
                continue
            queue_job(db, inbox, "dm_poll", automatic=True)


@router.post("/accounts/{account_id}/dm-inbox/poll", status_code=202, response_model=JobResponse)
def poll_inbox(account_id: str, db: Session = Depends(get_db), sender_id: str | None = None):
    inbox = can_poll(db, account_id, sender_id)
    previous = db.scalar(poll_jobs(db, inbox).where(Job.state.in_(["queued", "running"])).limit(1))
    if previous:
        return previous
    job = queue_job(db, inbox, "dm_poll")
    db.commit()
    return job


@router.get("/dm-submissions", response_model=Page[SubmissionResponse])
def list_submissions(
    account_id: str,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    required(db, Account, account_id)
    query = select(DmSubmission).where(DmSubmission.account_id == account_id)
    rows = db.scalars(
        query.order_by(DmSubmission.created_at.desc(), DmSubmission.id).limit(limit).offset(offset)
    ).all()
    items = []
    for row in rows:
        job = db.get(Job, row.job_id) if row.job_id else None
        draft = db.get(ContentDraft, row.draft_id) if row.draft_id else None
        discarded = bool(draft and draft.discarded_at)
        items.append(
            SubmissionResponse(
                id=row.id,
                sender_id=row.sender_id,
                media_ref=row.media_ref,
                draft_id=None if discarded else row.draft_id,
                job_id=row.job_id,
                created_at=row.created_at,
                state="draft_deleted"
                if discarded
                else "draft_ready"
                if draft
                else "reel_ready"
                if job and job.state == "succeeded" and job.payload.get("reel_url")
                else job.state
                if job
                else "unsupported",
                error=job.error if job else None,
                caption_status=(row.caption_command or {}).get("state"),
                caption_run=job.payload.get("caption_run") if job else None,
                command_status=(row.draft_commands or {}).get("status"),
                publication_hold=(row.draft_commands or {}).get("publication_hold"),
                music_checks=job.payload.get("music_checks", []) if job else [],
                skipped_videos=job.payload.get("skipped_videos", 0) if job else 0,
                reel_url=job.payload.get("reel_url") if job else None,
                reel_import_note=job.payload.get("reel_import_note") if job else None,
                reel_caption=(
                    (row.caption_command or {}).get("value") or job.payload.get("reel_caption")
                    if job and job.payload.get("reel_url")
                    else None
                ),
            )
        )
    return {
        "items": items,
        "total": db.scalar(select(func.count()).select_from(query.subquery())),
        "limit": limit,
        "offset": offset,
    }


@router.post("/dm-submissions/{submission_id}/import", status_code=202, response_model=JobResponse)
def retry_import(submission_id: str, db: Session = Depends(get_db)):
    submission = required(db, DmSubmission, submission_id)
    inbox = can_poll(db, submission.account_id, submission.sender_id)
    if submission.sender_id != inbox.sender_id or not submission.media_ref:
        raise HTTPException(409, "This submission no longer has an approved photo source")
    previous = db.get(Job, submission.job_id) if submission.job_id else None
    if previous and (
        submission.draft_id
        or previous.state in {"queued", "running"}
        or (
            previous.state == "succeeded"
            and previous.payload.get("reel_url")
            and not settings().dm_reel_ingest
        )
    ):
        return previous
    job = queue_job(db, inbox, "dm_import", submission_id=submission.id)
    submission.job_id = job.id
    db.commit()
    return job


def enabled_inbox(db, job):
    inbox = sender_inbox(db, job.account_id, job.payload.get("sender_id"))
    if not inbox or not inbox.enabled or inbox.version != job.payload.get("inbox_version"):
        raise OperationBlocked("DM inbox settings changed. Queue another read from Content")
    return inbox


def claim(db, lane, job_id):
    lane.assert_owned()
    db.scalar(select(Account).where(Account.id == lane.account_id).with_for_update())
    job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
    if (
        not job
        or job.account_id != lane.account_id
        or job.claim_owner != lane.owner
        or job.state != "running"
    ):
        raise OwnershipLost("The DM job is no longer owned by this worker")
    return job


def music_for_share(engine, lane, adapter, guard, source_music, caption, context, auto, playlist):
    checks = []
    if auto:
        original = source_music or {}
        canonical, native = original.get("id"), source_feed_track(original)
        original_title, original_artist = original.get("title"), original.get("artist")
        if canonical or native:
            checks.append(
                dict(title=original["title"], artist=original["artist"], state="source_matched")
            )
            return (None if native else canonical), native, None, checks
        source_metadata = bool(original_title and original_artist)
        mention = (
            (original_title, spotify_query(original_title, original_artist))
            if source_metadata
            else named_song(caption)
        )
        if mention:
            title, query = mention
            try:
                guard.reserve_dm_requests(1)
            except BudgetExhausted:
                track, state = None, "budget_exhausted"
            else:
                guard.music_queries = {query}
                evidence = original_artist if source_metadata else caption
                track, state = named_track(
                    adapter, title, query, evidence, allow_partial=not source_metadata
                )
            if track and source_metadata:
                state = "source_matched"
            checks.append(dict(title=title, artist=(track or {}).get("artist", ""), state=state))
            if track:
                return None, track, None, checks
        if source_metadata:
            context = f"{context} {original_title} {original_artist}"

    # The user can still force a playlist pick with the explicit playlist command.
    candidate = None
    if auto:
        with Session(engine) as db:
            candidate = audio_candidate(db, lane.account_id)
    if candidate and (not playlist or random.choice((True, False))):
        track, check = candidate
        return None, track, None, [*checks, check]
    if not playlist:
        return None, None, None, checks
    with Session(engine) as db:
        candidates = playlist_candidates(db, lane.account_id, playlist, context)
        used = db.execute(
            select(
                PlaylistSelection.catalog_id,
                PlaylistSelection.audio_asset_id,
                PlaylistSelection.audio_cluster_id,
            ).where(PlaylistSelection.account_id == lane.account_id)
        ).all()
        spent_audio = spent_audio_assets(db, lane.account_id)
    if not candidates:
        if candidate:
            track, check = candidate
            return None, track, None, [*checks, check]
        return None, None, None, checks
    try:
        guard.reserve_dm_requests(len(candidates))
    except BudgetExhausted:
        checks.extend(
            dict(spotify_id=id_, title=title, artist=artist, state="budget_exhausted")
            for id_, title, artist in candidates
        )
        if candidate:
            track, check = candidate
            return None, track, None, [*checks, check]
        return None, None, None, checks
    guard.music_queries = {spotify_query(title, artist) for _, title, artist in candidates}
    selection = match_track(
        adapter,
        candidates,
        {row.catalog_id for row in used if row.catalog_id},
        used_assets=spent_audio | {row.audio_asset_id for row in used if row.audio_asset_id},
        used_clusters={row.audio_cluster_id for row in used if row.audio_cluster_id},
        checks=checks,
    )
    track = selection.pop("feed_music") if selection else None
    if not track and candidate:
        track, check = candidate
        checks.append(check)
    return None, track, selection, checks


def prepare_replacement_music(engine, lane, job_id, adapter, guard, clock):
    config = settings()
    with Session(engine) as db:
        config = preferences(db, lane.account_id, config)
        job = db.get(Job, job_id)
        inbox = enabled_inbox(db, job)
        original = db.get(ContentDraft, job.payload["replacement_from"])
        target = db.get(ContentDraft, job.payload["replacement_draft_id"])
        submission = db.get(DmSubmission, job.payload["replacement_source_submission_id"])
        if (
            not original
            or not target
            or not submission
            or original.account_id != lane.account_id
            or target.account_id != lane.account_id
            or submission.account_id != lane.account_id
            or submission.sender_id != inbox.sender_id
            or submission.draft_id != original.id
            or original.status != "published"
            or target.status != "draft"
            or target.discarded_at
            or target.kind != original.kind
            or target.kind not in {"feed_photo", "feed_carousel"}
            or target.asset_id != original.asset_id
            or target.photo_assets != original.photo_assets
            or target.revision != job.payload["replacement_revision"]
        ):
            raise OperationBlocked("The replacement draft no longer matches the original post")
        playlists = [p.playlist_id for p in music_library(db, lane.account_id, config) if p.enabled]
        draft_id, revision, caption, source_music = (
            target.id,
            target.revision,
            target.caption,
            target.source_music,
        )
    canonical, native, selection, checks = music_for_share(
        engine, lane, adapter, guard, source_music, caption, caption, True, playlists
    )
    with Session(engine) as db, db.begin():
        job = claim(db, lane, job_id)
        enabled_inbox(db, job)
        target = db.scalar(
            select(ContentDraft).where(ContentDraft.id == draft_id).with_for_update()
        )
        if (
            not target
            or target.status != "draft"
            or target.discarded_at
            or target.revision != revision
        ):
            raise OperationBlocked("The replacement changed while checking music")
        job.payload = {**job.payload, "music_checks": checks}
        if canonical or native:
            target.music_canonical_id, target.playlist_music = canonical, native
            if selection:
                db.add(
                    PlaylistSelection(account_id=lane.account_id, draft_id=draft_id, **selection)
                )
            mark_used(db, lane.account_id, native, clock())
            target.revision += 1
            target.updated_at = clock()
            if not target.caption.strip():
                from .caption_pipeline import enqueue_caption

                enqueue_caption(db, target, job.payload["replacement_caption_source"])
    if not (canonical or native):
        raise OperationBlocked("No verified feed music matched this replacement")


def perform(engine, lane, job_id, adapter, guard, clock):
    config = settings()
    with Session(engine) as db:
        config = preferences(db, lane.account_id, config)
        job = db.get(Job, job_id)
        inbox = enabled_inbox(db, job)
        account = db.get(Account, lane.account_id)
        kind = job.kind
        if kind in {"dm_audio", "dm_replacement_music"}:
            scope = None
        elif kind == "dm_poll":
            if job.payload.get("page_saved") is True:
                return
            scope = (inbox.sender_id, account.instagram_id, inbox.thread_id, inbox.cursor)
        else:
            submission = db.get(DmSubmission, job.payload.get("submission_id"))
            if (
                not submission
                or submission.account_id != account.id
                or submission.sender_id != inbox.sender_id
                or submission.job_id != job.id
            ):
                raise OperationBlocked("The selected DM no longer matches this import job")
            if submission.draft_id:
                return
            submission_id, reference = submission.id, submission.media_ref
            share_at = job.payload.get("default_share_at")
            new_defaults = bool(
                share_at and config.dm_defaults_apply(account.id, datetime.fromisoformat(share_at))
            )
            commands = (submission.draft_commands or {}).get("commands", {})
            wants_caption = (new_defaults or config.dm_caption_v2) and not (
                submission.caption_command or commands.get("story")
            )
            playlist_id = [
                p.playlist_id for p in music_library(db, account.id, config) if p.enabled
            ]
            music_command = commands.get("music")
            auto_music = (
                (
                    new_defaults
                    or (config.dm_auto_music and config.dm_default_account_id == account.id)
                )
                and not commands.get("story")
                and not music_command
            )
            wants_playlist = not commands.get("story") and (
                music_command["value"] == "playlist"
                if music_command
                else auto_music and bool(playlist_id)
            )
            sender_caption = (submission.caption_command or {}).get("value", "")
            examples = (
                style_examples(db, account.id, config.dm_caption_sources)
                if wants_caption and config.dm_caption_model and not config.dm_caption_v2
                else []
            )
    if kind == "dm_audio":
        return verify_audio(engine, lane, job_id, adapter, guard)
    if kind == "dm_replacement_music":
        return prepare_replacement_music(engine, lane, job_id, adapter, guard, clock)
    if kind == "dm_poll":
        thread_id, next_cursor, messages = read_page(adapter.client, *scope)
        with Session(engine) as db, db.begin():
            job = claim(db, lane, job_id)
            inbox = enabled_inbox(db, job)
            record_page(db, inbox, thread_id, next_cursor, messages, clock())
            job.payload = {**job.payload, "page_saved": True}
        return

    try:
        images, caption, skipped_videos, source_music = read_shared_photos(
            adapter.client, reference, guard
        )
    except SharedReel as reel:
        if config.dm_reel_ingest:
            save_reel_video(engine, lane, job_id, submission_id, reel, config, guard)
        else:
            save_reel_share(engine, lane, job_id, submission_id, reel, config, examples, clock)
        return
    upload_backend(config)
    samples = []
    ocr_sample = None
    sample_photos = (
        wants_caption
        and not config.dm_caption_v2
        and config.dm_caption_vision_model
        and not has_editorial_context(caption)
    )
    asset_ids = []
    for raw in images:
        metadata = validate_image(raw)
        if sample_photos:
            if not samples:
                ocr_sample = image_sample(raw, edge=960)
            samples.append(image_sample(raw))
        with Session(engine) as db:
            asset = persist_asset(db, config, lane.account_id, raw, metadata)
            asset_ids.append(asset.id)
            db.commit()
    generated = None
    scene = []
    if wants_caption and not config.dm_caption_v2:
        caption_run = {
            "style_references": len(examples),
            "has_source_caption": bool(caption.strip()),
            "stages": {},
        }

        def report_caption(stage, detail):
            caption_run["stages"][stage] = detail
            with Session(engine) as db, db.begin():
                job = claim(db, lane, job_id)
                job.heartbeat_at = clock()
                job.lease_expires_at = job.heartbeat_at + timedelta(seconds=90)
                job.payload = {**job.payload, "caption_run": caption_run}

        generated = write_caption(
            config.dm_caption_model,
            config.dm_caption_vision_model,
            caption_image(samples),
            caption,
            examples,
            report=report_caption,
            observe=scene.append,
            ocr_image=ocr_sample,
        )
        if not generated and config.dm_caption_model and asset_ids:
            previous = caption_run["stages"].get("result", {})
            generated = archive_caption(caption, reference)
            report_caption(
                "fallback",
                {
                    "state": "complete",
                    "reason": previous.get("reason") or "no_valid_caption",
                },
            )
            report_caption("result", {"state": "complete", "reason": "archive_fallback"})
        if generated:
            generated = add_caption_tags(generated, caption, " ".join(scene))
    caption_context = "\n".join(filter(None, (caption, sender_caption)))
    music_id, feed_music, selection, checks = music_for_share(
        engine,
        lane,
        adapter,
        guard,
        source_music,
        caption_context,
        " ".join([caption_context, *scene]),
        auto_music,
        playlist_id if wants_playlist else None,
    )
    with Session(engine) as db:
        job = claim(db, lane, job_id)
        inbox = enabled_inbox(db, job)
        submission = db.get(DmSubmission, submission_id)
        if submission.job_id != job.id or submission.draft_id:
            raise OperationBlocked("The DM import changed while reading the image")
        if submission.caption_command:
            caption = submission.caption_command["value"]
            submission.caption_command = {**submission.caption_command, "state": "applied"}
        elif wants_caption and not config.dm_caption_v2:
            caption = generated or ""
            submission.caption_command = {
                "id": "local-caption",
                "value": caption,
                "state": "generated" if generated else "generation_failed",
            }
        carousel = len(asset_ids) > 1 or skipped_videos > 0
        draft = ContentDraft(
            account_id=lane.account_id,
            asset_id=asset_ids[0],
            kind="feed_carousel" if carousel else "feed_photo",
            caption=caption,
            source_music=source_music,
            music_canonical_id=music_id,
            playlist_music=feed_music,
            photo_assets=asset_ids if carousel else None,
            photo_order=list(range(1, len(asset_ids) + 1)) if carousel else None,
        )
        db.add(draft)
        db.flush()
        if selection:
            db.add(PlaylistSelection(account_id=lane.account_id, draft_id=draft.id, **selection))
            db.flush()
        mark_used(db, lane.account_id, feed_music, clock())
        submission.draft_id = draft.id
        job.payload = {**job.payload, "skipped_videos": skipped_videos, "music_checks": checks}
        pending_caption = wants_caption and config.dm_caption_v2 and not submission.caption_command
        if pending_caption:
            from .caption_pipeline import enqueue_caption

            enqueue_caption(
                db,
                draft,
                caption,
                submission_id=submission.id,
                import_id=job.id,
                automatic=new_defaults or bool((submission.draft_commands or {}).get("commands")),
            )
            draft.caption = ""
        if new_defaults:
            playlist_default = wants_playlist and not (music_id or (feed_music and not selection))
            apply_share_defaults(inbox, submission, draft, job.payload, playlist_default)
        if not pending_caption:
            apply_draft_commands(db, inbox, submission, draft)
        db.add(
            AuditEvent(
                account_id=lane.account_id,
                event="dm_draft_created",
                detail={"submission_id": submission.id, "draft_id": draft.id},
            )
        )
        db.commit()


def save_reel_video(engine, lane, job_id, submission_id, reel, config, guard):
    from .caption_pipeline import enqueue_caption

    data = None
    note = "Instagram did not provide a downloadable video. Upload an MP4 to prepare a Reel."
    if reel.video_url:
        try:
            data = download_reel(reel.video_url, guard)
            metadata = validate_video(data)
        except (Inaccessible, OperationBlocked) as error:
            data = None
            note = str(error)
    asset_id = None
    if data:
        with Session(engine) as db:
            asset = persist_asset(db, config, lane.account_id, data, metadata)
            asset_id = asset.id
            db.commit()

    with Session(engine) as db, db.begin():
        job = claim(db, lane, job_id)
        inbox = enabled_inbox(db, job)
        submission = db.get(DmSubmission, submission_id)
        if not submission or submission.job_id != job.id or submission.draft_id:
            raise OperationBlocked("The Reel import changed while reading the source")
        job.payload = {**job.payload, "reel_url": reel.url}
        if not asset_id:
            job.payload = {**job.payload, "reel_import_note": note}
            return
        existing = db.scalar(
            select(ContentDraft)
            .where(
                ContentDraft.account_id == lane.account_id,
                ContentDraft.kind == "feed_reel",
                ContentDraft.discarded_at.is_(None),
                or_(
                    ContentDraft.asset_id == asset_id,
                    ContentDraft.reel_source["url"].as_string() == reel.url,
                ),
            )
            .order_by(ContentDraft.created_at)
            .limit(1)
        )
        if existing:
            submission.draft_id = existing.id
            job.payload = {**job.payload, "reel_duplicate": True}
            return
        command = submission.caption_command or {}
        draft = ContentDraft(
            account_id=lane.account_id,
            asset_id=asset_id,
            kind="feed_reel",
            fit_mode="native",
            caption=command.get("value") or "",
            reel_source={"url": reel.url, "media_id": reel.media_id, "owner_id": reel.owner_id},
        )
        db.add(draft)
        db.flush()
        submission.draft_id = draft.id
        automatic = bool(job.payload.get("default_share_at"))
        if automatic:
            apply_share_defaults(inbox, submission, draft, job.payload)
        if command:
            submission.caption_command = {**command, "state": "applied"}
            draft.caption_review = {"state": "owner_caption", "reason": None}
            apply_draft_commands(db, inbox, submission, draft)
        else:
            enqueue_caption(
                db,
                draft,
                reel.caption,
                submission_id=submission.id,
                import_id=job.id,
                automatic=automatic,
            )


def complete_caption_submission(db, draft, payload, line, ready):
    submission = (
        db.get(DmSubmission, payload.get("submission_id")) if payload.get("submission_id") else None
    )
    if not submission or submission.draft_id != draft.id:
        return
    submission.caption_command = {
        "id": "local-caption",
        "value": line or "",
        "state": "generated" if ready else "generation_failed",
    }
    current_import = db.get(Job, payload["import_id"]) if payload.get("import_id") else None
    inbox = sender_inbox(db, draft.account_id, submission.sender_id)
    if (
        not payload.get("automatic")
        or not current_import
        or not inbox
        or not inbox.enabled
        or current_import.payload.get("inbox_version") != inbox.version
    ):
        return
    if ready:
        apply_draft_commands(db, inbox, submission, draft)
    else:
        submission.draft_commands = {
            **(submission.draft_commands or {}),
            "status": "review_publication",
            "publication_hold": (draft.caption_review or {}).get("reason"),
        }


def save_reel_share(engine, lane, job_id, submission_id, reel, config, examples, clock):
    with Session(engine) as db, db.begin():
        job = claim(db, lane, job_id)
        enabled_inbox(db, job)
        submission = db.get(DmSubmission, submission_id)
        if submission.job_id != job.id:
            raise OperationBlocked("The Reel import changed while reading the source")
        has_caption = bool(submission.caption_command)
        job.heartbeat_at = clock()
        job.lease_expires_at = job.heartbeat_at + timedelta(seconds=90)

    caption = None
    if reel.caption.strip() and not has_caption:
        line = write_caption(config.dm_caption_model, None, None, reel.caption, examples)
        caption = add_caption_tags(line or archive_caption(reel.caption, reel.url), reel.caption)

    with Session(engine) as db, db.begin():
        job = claim(db, lane, job_id)
        enabled_inbox(db, job)
        submission = db.get(DmSubmission, submission_id)
        if submission.job_id != job.id:
            raise OperationBlocked("The Reel import changed while preparing the caption")
        job.payload = {**job.payload, "reel_url": reel.url, "reel_caption": caption}


def apply_share_defaults(inbox, submission, draft, payload, wants_playlist=False):
    from .executor import aware

    saved = submission.draft_commands or {}
    commands = dict(saved.get("commands", {}))
    sent_at = payload["default_share_at"]
    if wants_playlist:
        commands.setdefault(
            "music", {"id": submission.message_id, "value": "playlist", "at": sent_at}
        )
    if len(draft.photo_assets or []) > 1:
        commands.setdefault(
            "order", {"id": submission.message_id, "value": "randomize", "at": sent_at}
        )
    if (
        "publish" not in commands
        and not commands.get("story")
        and inbox.direct_publish_from
        and datetime.fromisoformat(sent_at) >= aware(inbox.direct_publish_from)
    ):
        if (
            inbox.last_polled_at
            and not inbox.cursor
            and aware(inbox.last_polled_at)
            >= datetime.fromisoformat(payload["default_check_after"])
        ):
            commands["publish"] = {"id": submission.message_id, "value": "post", "at": sent_at}
        else:
            saved = {**saved, "default_held": True}
    submission.draft_commands = {**saved, "commands": commands}


def apply_caption(db, submission, command):
    if submission.caption_command and submission.caption_command["id"] == command["id"]:
        return
    state = "pending"
    if submission.draft_id:
        draft = db.scalar(
            select(ContentDraft).where(ContentDraft.id == submission.draft_id).with_for_update()
        )
        state = "needs_review"
        if draft.status == "draft" and not draft.discarded_at:
            if draft.caption != command["value"]:
                draft.caption, draft.updated_at = command["value"], now()
                draft.revision += 1
            if draft.caption_review:
                draft.caption_review = {
                    **draft.caption_review,
                    "state": "owner_caption",
                    "reason": None,
                }
            state = "applied"
    submission.caption_command = {"id": command["id"], "value": command["value"], "state": state}


def apply_draft_commands(db, inbox, submission, draft):
    from .content_publication import queue_publication
    from .executor import aware

    saved = submission.draft_commands or {}
    commands = saved.get("commands", {})
    if (
        (not commands and not saved.get("default_held"))
        or draft.status != "draft"
        or draft.discarded_at
    ):
        return
    caption_state = (draft.caption_review or {}).get("state")
    if caption_state == "processing":
        return
    if draft.kind == "feed_reel":
        apply_reel_commands(db, inbox, submission, draft, commands, saved)
        return
    story, music, publish, order, fit = (
        commands.get(key) for key in ("story", "music", "publish", "order", "fit")
    )
    kind = "story_photo" if story else draft.kind
    track = (music["value"] or None) if music else draft.music_canonical_id
    playlist_music = draft.playlist_music
    playlist_unavailable = False
    if track == "playlist":
        selection = db.get(PlaylistSelection, draft.id) if draft.id else None
        playlist_music = (
            playlist_music
            if selection
            and playlist_music
            and selection.audio_asset_id == playlist_music.get("audio_asset_id")
            else None
        )
        track = selection.catalog_id if selection and not playlist_music else None
        playlist_unavailable = not (track or playlist_music)
    elif music or story:
        playlist_music = None
    use_original = track == "original"
    if use_original:
        playlist_music = source_feed_track(draft.source_music)
        track = (draft.source_music or {}).get("id") if not playlist_music else None
        if not track and not playlist_music:
            submission.draft_commands = {**saved, "status": "source_music_unavailable"}
            return
    if story and not music:
        track = None
    photo_order = order["value"] if order else None
    if photo_order == "original":
        photo_order = list(range(1, len(draft.photo_assets or []) + 1)) or None
        order = order if photo_order else None
    if photo_order == "randomize" and draft.photo_assets and len(draft.photo_assets) > 1:
        photo_order = list(range(1, len(draft.photo_assets) + 1))
        random.Random(order["id"]).shuffle(photo_order)
        if photo_order == draft.photo_order:
            photo_order = photo_order[1:] + photo_order[:1]
        commands = {**commands, "order": {**order, "value": photo_order}}
        saved = {**saved, "commands": commands}
    if (
        "invalid" in commands
        or ((track or playlist_music) and kind == "story_photo")
        or (story and draft.photo_assets)
        or (order and (not isinstance(photo_order, list) or not is_photo_order(draft, photo_order)))
    ):
        submission.draft_commands = {**saved, "status": "review_commands"}
        return
    values = {"kind": kind, "music_canonical_id": track, "playlist_music": playlist_music}
    if story:
        values["overlay_text"] = story["value"]
    if order:
        values["photo_order"] = photo_order
    if fit:
        values["fit_mode"] = fit["value"]
    if any(getattr(draft, key) != value for key, value in values.items()):
        for key, value in values.items():
            setattr(draft, key, value)
        draft.revision += 1
        draft.updated_at = now()
    status = "default_held" if saved.get("default_held") else "edits_saved"
    hold = None
    if playlist_unavailable:
        status = "playlist_unavailable"
    elif publish and publish["value"] == "draft":
        status = "held_as_draft"
    elif publish:
        expects_story = publish["value"] == "story"
        allowed = (
            inbox.direct_publish_from
            and datetime.fromisoformat(publish["at"]) >= aware(inbox.direct_publish_from)
            and (publish["value"] == "autopost" or expects_story == (draft.kind == "story_photo"))
        )
        status = "review_publication"
        caption_ready = (submission.caption_command or {}).get("state") != "generation_failed" and (
            caption_state not in {"needs_review"}
            and (caption_state != "ready" or settings().dm_caption_autopost)
        )
        if allowed and caption_ready:
            try:
                account = db.get(Account, inbox.account_id)
                queue_publication(
                    db,
                    account,
                    draft,
                    draft.revision,
                    direct={
                        "inbox_version": inbox.version,
                        "submission_id": submission.id,
                        "sender_id": inbox.sender_id,
                    },
                )
            except HTTPException as exc:
                hold = str(exc.detail)
            else:
                status = "publication_queued"
        elif not caption_ready:
            hold = (
                "Review the generated caption. Automatic caption approval is paused during evaluation."
                if caption_state == "ready"
                else "Save a reviewed caption in the draft before publishing."
            )
        elif not allowed:
            hold = "This share needs owner review. Check the sender's direct publishing grant and the selected post or story format."
    submission.draft_commands = {**saved, "status": status, "publication_hold": hold}


def apply_reel_commands(db, inbox, submission, draft, commands, saved):
    from .content_publication import queue_publication
    from .executor import aware

    publish = commands.get("publish")
    if not publish:
        return
    if publish["value"] == "draft":
        submission.draft_commands = {**saved, "status": "held_as_draft"}
        return
    hold = "Reels need a caption and confirmation that the video and audio can be reused. Use ^ rights for this share."
    rights = commands.get("rights")
    if (
        publish["value"] in {"post", "autopost"}
        and inbox.direct_publish_from
        and inbox.sender_id == db.get(Account, draft.account_id).instagram_id
        and datetime.fromisoformat(publish["at"]) >= aware(inbox.direct_publish_from)
        and rights
        and rights["value"] == "confirmed"
        and datetime.fromisoformat(rights["at"]) >= aware(inbox.direct_publish_from)
        and (draft.caption_review or {}).get("state") in {"ready", "owner_caption"}
        and draft.caption.strip()
        and not (set(commands) & {"story", "music", "fit", "order", "invalid"})
        and not saved.get("default_held")
        and not inbox.cursor
    ):
        try:
            queue_publication(
                db,
                db.get(Account, draft.account_id),
                draft,
                draft.revision,
                direct={
                    "inbox_version": inbox.version,
                    "submission_id": submission.id,
                    "sender_id": inbox.sender_id,
                },
                rights_confirmed=True,
            )
        except HTTPException as error:
            hold = str(error.detail)
        else:
            submission.draft_commands = {**saved, "status": "publication_queued"}
            return
    submission.draft_commands = {**saved, "status": "review_publication", "publication_hold": hold}


def record_page(db, inbox, thread_id, next_cursor, messages, at):
    from .dm_receipts import queue_processing_receipt, receipt_identities
    from .executor import aware

    receipt_ids, receipt_contexts = receipt_identities(db, inbox.account_id)
    newest = inbox.scan_head_id or (messages[0].id if messages else None)
    checkpoint = inbox.pending_caption or {}
    pending = dict(checkpoint.get("commands", {}))
    if "id" in checkpoint:  # Caption-only checkpoint from the previous version.
        pending["caption"] = {"id": checkpoint["id"], "value": checkpoint["value"]}
    reached_watermark = bool(checkpoint.get("reached_watermark"))
    for message in messages:
        reached_watermark = reached_watermark or message.id == inbox.watermark
        if (reached_watermark and not pending) or message.timestamp < aware(inbox.created_at):
            next_cursor = None
            break
        if (
            message.sender_id != inbox.sender_id
            or message.id in receipt_ids
            or message.client_context in receipt_contexts
        ):
            continue
        if message.audio:
            if not reached_watermark:
                record_audio(db, inbox, message, at, thread_id)
            continue
        command = message.command
        if message.caption is not None:
            command = ("caption", message.caption)
        if command:
            if not reached_watermark:
                pending.setdefault(
                    command[0],
                    {"id": message.id, "value": command[1], "at": message.timestamp.isoformat()},
                )
            continue
        if not message.media_ref:
            pending = {}
            continue
        submission = db.scalar(
            select(DmSubmission).where(
                DmSubmission.account_id == inbox.account_id,
                DmSubmission.thread_id == thread_id,
                DmSubmission.message_id == message.id,
            )
        )
        if not submission and not reached_watermark:
            submission = DmSubmission(
                account_id=inbox.account_id,
                sender_id=message.sender_id,
                thread_id=thread_id,
                message_id=message.id,
                media_ref=message.media_ref,
            )
            db.add(submission)
            db.flush()
            job = queue_job(db, inbox, "dm_import", submission_id=submission.id)
            submission.job_id = job.id
            queue_processing_receipt(db, inbox, thread_id, job)
            config = preferences(db, inbox.account_id, settings())
            if config.dm_defaults_apply(inbox.account_id, message.timestamp):
                interval = config.dm_poll_interval_seconds
                job.payload = {
                    **job.payload,
                    "default_share_at": message.timestamp.isoformat(),
                    "default_check_after": (at + timedelta(seconds=interval * 0.75)).isoformat(),
                }
                job.scheduled_at = at + timedelta(seconds=interval + 15)
                job.priority = -1  # Process the follow-up inbox scan before importing this share.
        if pending and submission:
            if caption := pending.get("caption"):
                apply_caption(db, submission, caption)
            edits = {key: value for key, value in pending.items() if key != "caption"}
            if edits:
                submission.draft_commands = {"commands": edits, "status": "pending"}
                if submission.draft_id:
                    apply_draft_commands(
                        db, inbox, submission, db.get(ContentDraft, submission.draft_id)
                    )
        pending = {}
    if reached_watermark and not pending:
        next_cursor = None
    inbox.thread_id, inbox.cursor, inbox.last_polled_at = thread_id, next_cursor, at
    inbox.pending_caption = (
        {"commands": pending, "reached_watermark": reached_watermark}
        if next_cursor and pending
        else None
    )
    if next_cursor:
        inbox.scan_head_id = newest
    else:
        inbox.watermark, inbox.scan_head_id = newest or inbox.watermark, None
