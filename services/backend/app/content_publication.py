"""Revision approvals, one-shot publication, and positive read reconciliation."""

import re
import tempfile
from datetime import timedelta
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import settings
from .content_drafts import MAX_CAROUSEL_PHOTOS, DraftValues, is_photo_order
from .content_images import carousel_frame, render_image
from .content_storage import read_original
from .content_video import probe_video, reel_session_id, video_frame
from .dashboard import required
from .db import get_db
from .execution_errors import OperationBlocked, OutcomePending, OwnershipLost
from .models import (
    Account,
    Action,
    ActionAttempt,
    AttemptEvent,
    AuditEvent,
    ContentAsset,
    ContentDraft,
    Job,
    now,
)
from .schemas import JobResponse, Page, Strict

router = APIRouter(prefix="/api/v1")
PUBLICATIONS = {
    "feed_photo": "publish_photo",
    "story_photo": "publish_story",
    "feed_carousel": "publish_carousel",
    "feed_reel": "publish_reel",
}
CONFIGURE = {
    "publish_photo": "configure_photo",
    "publish_story": "configure_story",
    "publish_carousel": "configure_carousel",
    "publish_reel": "configure_reel",
}
REMOVAL_KINDS = {"delete_post", "delete_story", "archive_post"}
WRITE_KINDS = set(CONFIGURE) | {"edit_caption"} | REMOVAL_KINDS
CONTENT_KINDS = WRITE_KINDS | {"reconcile_content"}


class PublicationCheckFailed(OutcomePending):
    """A local verification explanation suitable for the owner dashboard."""


class ContentOperations(Page[JobResponse]):
    blocking_job: JobResponse | None


class PublicationApproval(Strict):
    expected_revision: int = Field(ge=1)
    reviewed: Literal[True]
    rights_confirmed: bool = False


class MediaApproval(PublicationApproval):
    operation: Literal["edit_caption", "delete_story", "delete_post"]
    caption: str = Field(default="", max_length=2200)
    idempotency_key: str = Field(min_length=8, max_length=200)


class ArchiveReplacementApproval(Strict):
    expected_revision: int = Field(ge=1)
    replacement_id: str = Field(min_length=36, max_length=36)
    playback_confirmed: Literal[True]


class ReconcileApproval(Strict):
    job_id: str
    media_ref: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def reference(self):
        if self.media_ref and not re.fullmatch(
            r"(?:[0-9]{1,32}(?:_[0-9]{1,32})?|https://(?:www\.)?instagram\.com/(?:(?:p|reel)/[A-Za-z0-9_-]+/|stories/[A-Za-z0-9_.]+/[0-9]+/))",
            self.media_ref,
        ):
            raise ValueError("Use a media ID, Instagram post URL or Instagram story URL")
        return self


def snapshot(draft):
    result = {key: getattr(draft, key) for key in DraftValues.model_fields}
    if result["music_canonical_id"] is None:
        result.pop("music_canonical_id")
    if draft.playlist_music:
        result["playlist_music"] = draft.playlist_music
    return result


def carousel_assets(draft):
    if (
        not draft.photo_assets
        or not 2 <= len(draft.photo_assets) <= MAX_CAROUSEL_PHOTOS
        or not draft.photo_order
        or not is_photo_order(draft, draft.photo_order)
    ):
        return []
    return [draft.photo_assets[number - 1] for number in draft.photo_order]


def supports_change(kind, operation):
    if operation == "delete_story":
        return kind == "story_photo"
    if operation in {"delete_post", "archive_post"}:
        return kind in {"feed_photo", "feed_carousel"}
    return kind == "feed_photo" and operation == "edit_caption"


def verify_carousel(intent, observation):
    observed_ids = observation.get("child_media_ids", [])
    receipt_ids = intent.get("child_media_ids")
    if (
        not receipt_ids
        or len(set(observed_ids)) != len(intent["asset_ids"])
        or receipt_ids != observed_ids
    ):
        raise PublicationCheckFailed("Instagram did not confirm the approved carousel photo order.")


def music_confirmed(expected, observation):
    if track := expected.get("playlist_music"):
        observed = observation.get("playlist_music") or {}
        return observed.get("audio_asset_id") == track["audio_asset_id"] and observed.get(
            "audio_cluster_id"
        ) in {None, track["audio_cluster_id"]}
    canonical = expected.get("music_canonical_id")
    return not canonical or observation.get("music_canonical_id") == canonical


def soundtrack_status(intent, observation):
    expected = intent.get("snapshot", {})
    if music_confirmed(expected, observation):
        return (
            "confirmed"
            if expected.get("playlist_music") or expected.get("music_canonical_id")
            else None
        )
    # owned_media already verifies ownership. Only an exact configure receipt
    # and the saved feed composition can establish publication independently.
    if (
        expected.get("kind") in {"feed_photo", "feed_carousel"}
        and intent.get("media_id") == observation.get("media_id")
        and intent.get("media_id")
        and expected.get("kind") == observation.get("kind")
        and expected.get("caption") == observation.get("caption")
        and not observation.get("playlist_music")
        and not observation.get("music_canonical_id")
    ):
        return "unverified"
    raise PublicationCheckFailed(
        "Instagram did not confirm the approved soundtrack or exact publication receipt."
    )


def ready(account, writes=True):
    if (
        not account
        or account.mode != "live"
        or account.session_state != "connected"
        or account.restricted
        or not account.read_only_verified_at
    ):
        raise HTTPException(409, "A validated connected Instagram account is required")
    if writes and settings().adapter_mode != "live":
        raise HTTPException(409, "Publication is paused on read-only transport")


def unresolved(db, account_id, except_job=None):
    return db.scalar(
        select(Job.id)
        .where(
            Job.account_id == account_id,
            Job.kind.in_(WRITE_KINDS),
            Job.id != (except_job or ""),
            Job.dispatch_started_at.is_not(None),
            Job.state.in_(["running", "unknown", "blocked"]),
        )
        .limit(1)
    )


def locked_draft(db, draft_id):
    existing = required(db, ContentDraft, draft_id)
    account = db.scalar(select(Account).where(Account.id == existing.account_id).with_for_update())
    draft = db.scalar(select(ContentDraft).where(ContentDraft.id == draft_id).with_for_update())
    return account, draft


def enqueue_content(db, account, draft, kind, payload, key):
    previous = db.scalar(
        select(Job).where(Job.account_id == account.id, Job.idempotency_key == key)
    )
    if previous:
        if any(previous.payload.get(field) != value for field, value in payload.items()):
            raise HTTPException(409, "This revision already has a different action intent")
        return previous
    if unresolved(db, account.id):
        raise HTTPException(
            409, "Reconcile the earlier uncertain media action before approving another"
        )
    job = Job(
        account_id=account.id,
        kind=kind,
        idempotency_key=key,
        payload={
            "draft_id": draft.id,
            "revision": draft.revision,
            "approved_at": now().isoformat(),
            **payload,
        },
    )
    db.add(job)
    db.flush()
    db.add(
        AuditEvent(
            account_id=account.id,
            event="content_action_approved",
            detail={
                "draft_id": draft.id,
                "revision": draft.revision,
                "job_id": job.id,
                "kind": kind,
            },
        )
    )
    return job


@router.post("/content-drafts/{draft_id}/publish", status_code=202, response_model=JobResponse)
def approve_publication(draft_id: str, body: PublicationApproval, db: Session = Depends(get_db)):
    account, draft = locked_draft(db, draft_id)
    job = queue_publication(
        db, account, draft, body.expected_revision, rights_confirmed=body.rights_confirmed
    )
    db.commit()
    return job


def queue_publication(
    db, account, draft, expected_revision, direct=None, *, rights_confirmed=False
):
    ready(account)
    if draft.discarded_at or draft.kind not in PUBLICATIONS:
        raise HTTPException(409, "Choose an available draft for publication")
    ordered_assets = carousel_assets(draft) if draft.kind == "feed_carousel" else []
    if draft.kind == "feed_carousel" and not ordered_assets:
        raise HTTPException(409, "A published carousel needs at least two photos in a valid order")
    if expected_revision != draft.revision:
        raise HTTPException(409, "Draft changed; review the current preview before publishing")
    if draft.publication_job_id:
        previous = required(db, Job, draft.publication_job_id)
        if previous.payload.get("revision") != expected_revision:
            raise HTTPException(
                409, "This draft already has a publication; use its own-media controls"
            )
        return previous
    if draft.status != "draft":
        raise HTTPException(409, "This revision cannot be published")
    asset = None
    if draft.kind == "feed_reel":
        asset = db.get(ContentAsset, draft.asset_id)
        if not asset or asset.account_id != account.id or asset.mime != "video/mp4":
            raise HTTPException(409, "This Reel has no validated video")
        if not rights_confirmed:
            raise HTTPException(409, "Confirm permission to publish this video and its audio")
        if (draft.caption_review or {}).get("state") == "processing" or not draft.caption.strip():
            raise HTTPException(409, "Save a caption before publishing this Reel")
        if direct and (draft.caption_review or {}).get("state") not in {"ready", "owner_caption"}:
            raise HTTPException(409, "Review the Reel caption in Archive Desk")
        if direct and not reel_autopost_ready(db, account.id):
            raise HTTPException(
                409, "Reel autopost requires a verified reviewed Reel and the autopost setting"
            )
    # Numeric upload IDs are allocated under the account lock and persisted
    # before any upload. They are never regenerated by the worker.
    upload_id = str(int(now().timestamp() * 1000))
    previous = db.scalars(
        select(Job).where(Job.account_id == account.id, Job.kind.in_(CONFIGURE))
    ).all()
    upload_id = str(
        max(
            int(upload_id),
            max(
                (
                    int(j.payload["upload_id"]) + len(j.payload.get("child_upload_ids", [])) + 1
                    for j in previous
                ),
                default=0,
            ),
        )
    )
    payload = {"snapshot": snapshot(draft), "asset_id": draft.asset_id, "upload_id": upload_id}
    if asset:
        payload.update(
            reel_rights=True,
            video_bytes=asset.byte_size,
            video_sha256=asset.sha256,
            reel_session=reel_session_id(account.instagram_id, upload_id),
        )
    if ordered_assets:
        payload["asset_ids"] = ordered_assets
        payload["child_upload_ids"] = [
            str(int(upload_id) + offset) for offset in range(1, len(ordered_assets) + 1)
        ]
    if direct:
        payload["dm_publication"] = direct
    job = enqueue_content(
        db,
        account,
        draft,
        PUBLICATIONS[draft.kind],
        payload,
        f"publish:{draft.id}:{draft.revision}",
    )
    draft.status, draft.publication_job_id, draft.updated_at = "queued", job.id, now()
    return job


def reel_autopost_ready(db, account_id):
    config = settings()
    return (
        config.dm_reel_autopost
        and config.dm_caption_autopost
        and config.dm_default_account_id == account_id
        and db.scalar(
            select(Job.id)
            .where(
                Job.account_id == account_id,
                Job.kind == "publish_reel",
                Job.state == "succeeded",
                Job.payload["dm_publication"].as_string().is_(None),
            )
            .limit(1)
        )
        is not None
    )


@router.post("/content-drafts/{draft_id}/media-action", status_code=202, response_model=JobResponse)
def approve_media_change(draft_id: str, body: MediaApproval, db: Session = Depends(get_db)):
    account, draft = locked_draft(db, draft_id)
    ready(account)
    if (
        draft.discarded_at
        or body.expected_revision != draft.revision
        or draft.status != "published"
        or not draft.media_id
    ):
        raise HTTPException(409, "Choose a confirmed publication and its current saved revision")
    if not supports_change(draft.kind, body.operation):
        raise HTTPException(409, "Choose an action that matches this publication")
    pending = db.scalar(
        select(Job.id)
        .where(
            Job.account_id == account.id,
            Job.kind.in_(WRITE_KINDS),
            Job.state.in_(["queued", "running"]),
        )
        .limit(1)
    )
    if pending:
        raise HTTPException(409, "Wait for the pending media action before approving another")
    job = enqueue_content(
        db,
        account,
        draft,
        body.operation,
        {"media_id": draft.media_id, "caption": body.caption},
        f"{body.operation}:{draft.id}:{body.idempotency_key}",
    )
    db.commit()
    return job


def verified_replacement(db, original, replacement_id):
    replacement = db.get(ContentDraft, replacement_id)
    if (
        not replacement
        or replacement.account_id != original.account_id
        or replacement.id == original.id
        or replacement.status != "published"
        or replacement.discarded_at
        or not replacement.media_id
        or replacement.media_id == original.media_id
        or replacement.kind != original.kind
        or replacement.asset_id != original.asset_id
        or replacement.photo_assets != original.photo_assets
        or replacement.photo_order != original.photo_order
    ):
        return False
    publication = (
        db.get(Job, replacement.publication_job_id) if replacement.publication_job_id else None
    )
    if (
        not publication
        or publication.account_id != original.account_id
        or publication.state != "succeeded"
    ):
        return False
    saved = publication.payload.get("snapshot", {})
    if not (
        saved.get("caption") and (saved.get("playlist_music") or saved.get("music_canonical_id"))
    ):
        return False
    return (
        db.scalar(
            select(AuditEvent.id)
            .where(
                AuditEvent.account_id == original.account_id,
                AuditEvent.event == "photo_replacement_prepared",
                AuditEvent.detail["original_id"].as_string() == original.id,
                AuditEvent.detail["replacement_id"].as_string() == replacement.id,
            )
            .limit(1)
        )
        is not None
    )


@router.post(
    "/content-drafts/{draft_id}/archive-replaced", status_code=202, response_model=JobResponse
)
def archive_replaced(
    draft_id: str, body: ArchiveReplacementApproval, db: Session = Depends(get_db)
):
    account, draft = locked_draft(db, draft_id)
    ready(account)
    if (
        draft.status != "published"
        or draft.discarded_at
        or not draft.media_id
        or draft.revision != body.expected_revision
        or not supports_change(draft.kind, "archive_post")
        or not verified_replacement(db, draft, body.replacement_id)
    ):
        raise HTTPException(409, "Choose the published original and its verified replacement")
    job = enqueue_content(
        db,
        account,
        draft,
        "archive_post",
        {
            "media_id": draft.media_id,
            "replacement_id": body.replacement_id,
            "playback_confirmed": body.playback_confirmed,
        },
        f"archive-replaced:{draft.id}:{draft.revision}",
    )
    db.commit()
    return job


@router.get("/content-operations", response_model=ContentOperations)
def list_operations(account_id: str, db: Session = Depends(get_db)):
    required(db, Account, account_id)
    query = select(Job).where(Job.account_id == account_id, Job.kind.in_(CONTENT_KINDS))
    jobs = db.scalars(query.order_by(Job.created_at.desc(), Job.id).limit(100)).all()
    blocker_id = unresolved(db, account_id)
    return {
        "items": jobs,
        "total": db.scalar(select(func.count()).select_from(query.subquery())),
        "limit": 100,
        "offset": 0,
        "blocking_job": db.get(Job, blocker_id) if blocker_id else None,
    }


@router.post("/content-drafts/{draft_id}/reconcile", status_code=202, response_model=JobResponse)
def request_reconciliation(draft_id: str, body: ReconcileApproval, db: Session = Depends(get_db)):
    account, draft = locked_draft(db, draft_id)
    ready(account, writes=False)
    parent = required(db, Job, body.job_id)
    if (
        parent.account_id != account.id
        or parent.kind not in WRITE_KINDS
        or parent.payload.get("draft_id") != draft.id
        or parent.state not in {"unknown", "blocked"}
        or not parent.dispatch_started_at
    ):
        raise HTTPException(409, "This action is not awaiting outcome reconciliation")
    media_ref = parent.payload.get("media_id") or draft.media_id or body.media_ref
    if not media_ref:
        raise HTTPException(
            409,
            "Review Instagram and supply the actual published media link; the app will not guess from recent posts",
        )
    pending = db.scalar(
        select(Job).where(
            Job.account_id == account.id,
            Job.kind == "reconcile_content",
            Job.state.in_(["queued", "running"]),
        )
    )
    if pending:
        return pending
    job = Job(
        account_id=account.id,
        kind="reconcile_content",
        idempotency_key=f"reconcile-content:{parent.id}:{now().isoformat()}",
        payload={
            "draft_id": draft.id,
            "revision": draft.revision,
            "parent_id": parent.id,
            "parent_kind": parent.kind,
            "media_ref": str(media_ref),
        },
    )
    db.add(job)
    db.commit()
    return job


def validate_intent(db, job):
    draft = db.get(ContentDraft, job.payload.get("draft_id"))
    if not draft or draft.discarded_at or draft.account_id != job.account_id:
        raise OperationBlocked("Content approval does not identify this account's draft")
    if job.kind == "reconcile_content":
        return draft
    if not job.payload.get("approved_at") or draft.revision != job.payload.get("revision"):
        raise OperationBlocked("Media action approval is missing or its revision changed")
    if job.kind == "archive_post" and (
        draft.status != "published"
        or job.payload.get("playback_confirmed") is not True
        or not verified_replacement(db, draft, job.payload.get("replacement_id"))
    ):
        raise OperationBlocked("The original no longer has an approved replacement")
    if direct := job.payload.get("dm_publication"):
        from .dm_intake import sender_inbox

        inbox = sender_inbox(db, job.account_id, direct.get("sender_id"))
        if (
            not inbox
            or not inbox.enabled
            or not inbox.direct_publish_from
            or inbox.version != direct["inbox_version"]
        ):
            raise OperationBlocked("Direct publication permission changed")
        if job.kind == "publish_reel" and not reel_autopost_ready(db, job.account_id):
            raise OperationBlocked("Reel autopost permission changed")
        if (draft.caption_review or {}).get(
            "state"
        ) == "ready" and not settings().dm_caption_autopost:
            raise OperationBlocked("Caption-driven autopost is awaiting review")
    if unresolved(db, job.account_id, job.id):
        raise OutcomePending("An earlier media action needs reconciliation")
    if job.kind in CONFIGURE:
        if (
            draft.publication_job_id != job.id
            or draft.status != "queued"
            or draft.asset_id != job.payload.get("asset_id")
            or snapshot(draft) != job.payload.get("snapshot")
            or (
                job.kind == "publish_carousel"
                and carousel_assets(draft) != job.payload.get("asset_ids")
            )
        ):
            raise OperationBlocked("The approved publication composition changed")
    elif draft.status != "published" or draft.media_id != job.payload.get("media_id"):
        raise OperationBlocked("The approved own-media target changed")
    return draft


def guard_media(db, job, attempt, metadata, guard, at):
    validate_intent(db, job)
    operation = metadata.get("operation")
    if job.kind not in WRITE_KINDS or attempt.dispatch_started_at:
        raise OperationBlocked("This media action cannot dispatch again")
    if operation in {"reel_settings", "reel_init", "reel_video"}:
        expected = job.payload.get("reel_session")
        if job.kind != "publish_reel" or not expected or not job.payload.get("reel_rights"):
            raise OperationBlocked("This job has no approved Reel upload")
        stages = {"reel_settings": None, "reel_init": "reel_settings", "reel_video": "reel_init"}
        valid = (
            metadata["session"] == expected
            and metadata["target_id"] == db.get(Account, job.account_id).instagram_id
            if operation == "reel_settings"
            else metadata.get("upload_id") == job.payload["upload_id"]
            and metadata["session"] == expected.replace("-", "")
            and metadata["size"] == job.payload["video_bytes"]
            and (operation != "reel_video" or metadata["sha256"] == job.payload["video_sha256"])
        )
        if not valid or job.payload.get("reel_stage") != stages[operation]:
            raise OperationBlocked("Reel upload does not match the approved video and stage")
        job.payload = {**job.payload, "reel_stage": operation}
        return False
    if operation == "upload":
        if job.kind == "publish_carousel":
            children = job.payload["child_upload_ids"]
            sent = job.payload.get("child_uploads_sent", [])
            if (
                not metadata.get("sidecar")
                or len(sent) >= len(children)
                or metadata.get("upload_id") != children[len(sent)]
            ):
                raise OperationBlocked("Upload does not match the next approved carousel photo")
            job.payload = {**job.payload, "child_uploads_sent": [*sent, metadata["upload_id"]]}
            return False
        if (
            job.kind not in {"publish_photo", "publish_story", "publish_reel"}
            or (job.kind == "publish_reel" and job.payload.get("reel_stage") != "reel_video")
            or metadata.get("sidecar")
            or metadata.get("upload_id") != job.payload.get("upload_id")
            or job.payload.get("upload_sent")
        ):
            raise OperationBlocked("Upload does not match its single approved publication")
        job.payload = {**job.payload, "upload_sent": True}
        return False
    if operation in CONFIGURE.values():
        carousel_ready = (
            job.kind == "publish_carousel"
            and job.payload.get("child_uploads_sent") == job.payload["child_upload_ids"]
            and metadata.get("child_upload_ids") == job.payload["child_upload_ids"]
            and metadata.get("client_sidecar_id") == job.payload["upload_id"]
        )
        if (
            operation != CONFIGURE.get(job.kind)
            or not (
                carousel_ready if job.kind == "publish_carousel" else job.payload.get("upload_sent")
            )
            or metadata.get("upload_id") != job.payload.get("upload_id")
            or metadata.get("caption") != job.payload["snapshot"]["caption"]
            or metadata.get("music_canonical_id")
            != job.payload["snapshot"].get("music_canonical_id")
            or metadata.get("playlist_music") != job.payload["snapshot"].get("playlist_music")
        ):
            raise OperationBlocked("Configuration does not match the reviewed publication")
    elif operation == "edit_caption" or operation in REMOVAL_KINDS:
        if (
            operation != job.kind
            or metadata.get("media_id") != job.payload.get("media_id")
            or getattr(guard, "verified_media", None) != job.payload.get("media_id")
        ):
            raise OperationBlocked("Own-media action lacks a fresh ownership check")
        if operation == "edit_caption" and metadata.get("caption") != job.payload.get("caption"):
            raise OperationBlocked("Caption does not match the approved change")
    else:
        raise OperationBlocked("Unsupported media mutation")
    attempt.dispatch_started_at = job.dispatch_started_at = at
    db.add(
        AttemptEvent(
            attempt_id=attempt.id,
            event="dispatch_started",
            detail={"operation": operation, "revision": job.payload["revision"]},
        )
    )
    return True


def verify_reconciled_reel(parent, draft, observation):
    from .executor import aware

    observed_id = observation["media_id"].split("_", 1)[0]
    known_id = parent.payload.get("media_id") or draft.media_id
    source_id = ((draft.reel_source or {}).get("media_id") or "").split("_", 1)[0]
    taken_at = getattr(observation.get("media"), "taken_at", None)
    dispatch = aware(parent.dispatch_started_at)
    # Without a returned ID, a manually supplied link must identify a new Reel
    # made during this dispatch. The caller has already confirmed the owner.
    if (
        observation["caption"] != parent.payload["snapshot"]["caption"]
        or observed_id == source_id
        or (
            observed_id != known_id.split("_", 1)[0]
            if known_id
            else not taken_at
            or not dispatch - timedelta(seconds=5)
            <= aware(taken_at)
            <= dispatch + timedelta(minutes=10)
        )
    ):
        raise PublicationCheckFailed(
            "The linked Reel does not match the approved caption and publication evidence."
        )


def finish_draft(db, job, state):
    if job.kind not in CONFIGURE:
        return
    draft = db.get(ContentDraft, job.payload.get("draft_id"))
    if draft and draft.publication_job_id == job.id and state != "succeeded":
        draft.status = (
            "unknown"
            if job.dispatch_started_at
            else "cancelled"
            if state == "cancelled"
            else "failed"
        )
        draft.updated_at = now()


def perform(engine, lane, job_id, adapter, guard, clock):
    music_status = None
    with Session(engine) as db:
        job = db.get(Job, job_id)
        draft = validate_intent(db, job)
        kind, payload, actor = (
            job.kind,
            dict(job.payload),
            db.get(Account, job.account_id).instagram_id,
        )
        source_media_id = ((draft.reel_source or {}).get("media_id") or "").split("_", 1)[0]
        assets = []
        # Rendering is local; release the read transaction before filesystem work.
        if kind in CONFIGURE:
            for asset_id in payload.get("asset_ids", [draft.asset_id]):
                asset = db.get(ContentAsset, asset_id)
                if not asset or asset.account_id != job.account_id:
                    raise OperationBlocked("An approved photo is unavailable")
                if asset in db:
                    db.expunge(asset)
                assets.append(asset)
    if kind in CONFIGURE:
        guard.approved_music = payload["snapshot"].get("music_canonical_id")
        guard.feed_music = payload["snapshot"].get("playlist_music")
        config = settings()
        frame = (
            carousel_frame(config, assets[0], payload["snapshot"])
            if kind == "publish_carousel"
            else None
        )
        with tempfile.TemporaryDirectory(prefix="archive-publication-") as directory:
            if kind == "publish_reel":
                video, cover = Path(directory) / "reel.mp4", Path(directory) / "cover.jpg"
                video.write_bytes(read_original(config, assets[0]))
                info = probe_video(video)
                cover.write_bytes(video_frame(video, info["duration"] * 0.15))
                result = adapter.publish_reel(
                    video, cover, payload["upload_id"], info, payload["snapshot"]["caption"]
                )
            elif kind == "publish_carousel":
                paths = []
                for index, asset in enumerate(assets):
                    path = Path(directory) / f"photo-{index + 1}.jpg"
                    path.write_bytes(render_image(config, asset, payload["snapshot"], frame))
                    paths.append(path)
                result = adapter.publish_carousel(
                    paths, payload["upload_id"], payload["child_upload_ids"], payload["snapshot"]
                )
            else:
                path = Path(directory) / "photo.jpg"
                path.write_bytes(render_image(config, assets[0], payload["snapshot"], frame))
                result = adapter.publish_photo(path, payload["upload_id"], payload["snapshot"])
        media_id = result["media_id"]
        # Keep the returned ID before the verification read. A read failure
        # cannot erase the configure receipt and trigger another publication.
        with Session(engine) as db, db.begin():
            lane.assert_owned()
            current = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
            if current.claim_owner != lane.owner:
                raise OwnershipLost("Publication owner changed before receipt persistence")
            current.payload = {**current.payload, **result}
            db.get(ContentDraft, payload["draft_id"]).media_id = media_id
        observation = adapter.owned_media(media_id, actor)
        if observation["kind"] != payload["snapshot"]["kind"]:
            raise PublicationCheckFailed(
                "The returned media type does not confirm the approved publication"
            )
        if kind == "publish_carousel":
            verify_carousel(payload | result, observation)
        if kind == "publish_reel" and (
            observation["caption"] != payload["snapshot"]["caption"]
            or observation["media_id"].split("_", 1)[0] == source_media_id
        ):
            raise PublicationCheckFailed(
                "Instagram did not confirm the new Reel and approved caption."
            )
        music_status = soundtrack_status(payload | result, observation)
    elif kind == "reconcile_content":
        media_id = adapter.resolve_media_ref(payload["media_ref"])
        observation = (
            adapter.archived_media(media_id, actor)
            if payload.get("parent_kind") == "archive_post"
            else adapter.owned_media(media_id, actor)
        )
        if not observation:
            raise PublicationCheckFailed("Instagram has not confirmed the archived original.")
    else:
        media_id = payload["media_id"]
        observation = adapter.owned_media(media_id, actor)
        guard.verified_media = media_id
        if kind in REMOVAL_KINDS:
            if not supports_change(observation["kind"], kind):
                raise OperationBlocked("The media type does not match the approved removal")
            guard.media_removal_kind = kind
            if kind == "archive_post":
                adapter.archive_post(media_id)
                if not adapter.archived_media(media_id, actor):
                    raise PublicationCheckFailed(
                        "Instagram has not confirmed the archived original."
                    )
            else:
                remove = adapter.delete_post if kind == "delete_post" else adapter.delete_story
                remove(media_id)
        else:
            if observation["kind"] != "feed_photo":
                raise OperationBlocked("Only a confirmed photo post caption can be edited here")
            adapter.edit_caption(media_id, payload["caption"], observation)
            observation = adapter.owned_media(media_id, actor)
            if observation["caption"] != payload["caption"]:
                raise PublicationCheckFailed("Instagram did not confirm the edited caption.")
    with Session(engine) as db, db.begin():
        lane.assert_owned()
        db.scalar(select(Account).where(Account.id == lane.account_id).with_for_update())
        current = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
        if current.claim_owner != lane.owner or current.state != "running":
            raise OwnershipLost("Media job ownership changed before observation commit")
        draft = db.get(ContentDraft, payload["draft_id"])
        if kind == "reconcile_content":
            parent = db.get(Job, payload["parent_id"])
            if (
                not parent
                or parent.account_id != lane.account_id
                or parent.payload.get("draft_id") != draft.id
                or parent.state not in {"unknown", "blocked"}
            ):
                raise OperationBlocked("Original media action is no longer awaiting reconciliation")
            if parent.kind in REMOVAL_KINDS - {"archive_post"}:
                raise PublicationCheckFailed(
                    "The media is still accessible. Removal remains unconfirmed."
                )
            if (
                parent.kind == "edit_caption"
                and observation["caption"] != parent.payload["caption"]
            ):
                raise PublicationCheckFailed(
                    "The requested caption is not confirmed; do not replay the write"
                )
            expected_kind = parent.payload.get("snapshot", {}).get("kind", "feed_photo")
            if parent.kind != "archive_post" and observation["kind"] != expected_kind:
                raise OperationBlocked(
                    "The linked media does not match the approved publication type"
                )
            if parent.kind == "publish_carousel":
                verify_carousel(parent.payload, observation)
            if parent.kind == "publish_reel":
                verify_reconciled_reel(parent, draft, observation)
            if parent.kind in CONFIGURE:
                music_status = soundtrack_status(parent.payload, observation)
            parent.state, parent.error, parent.completed_at = "succeeded", None, clock()
            parent.payload = {
                **parent.payload,
                "media_id": observation["media_id"],
                "reconciled_at": clock().isoformat(),
                "owner_linked": bool(not draft.media_id),
                "music_status": music_status,
            }
            summary = db.scalar(select(Action).where(Action.job_id == parent.id))
            if summary:
                summary.outcome, summary.detail = (
                    "succeeded",
                    "Owner-linked media verified without another write",
                )
            attempt = db.scalar(
                select(ActionAttempt)
                .where(ActionAttempt.job_id == parent.id)
                .order_by(ActionAttempt.started_at.desc())
                .limit(1)
            )
            if attempt:
                db.add(
                    AttemptEvent(
                        attempt_id=attempt.id,
                        event="content_reconciled",
                        detail={"media_id": observation["media_id"]},
                    )
                )
            kind = parent.kind
        if kind in REMOVAL_KINDS:
            draft.status = "archived" if kind == "archive_post" else "deleted"
        else:
            draft.media_id, draft.status = observation["media_id"], "published"
            if kind == "edit_caption":
                draft.caption, draft.revision = observation["caption"], draft.revision + 1
        draft.updated_at = clock()
        if current.kind == "reconcile_content" and kind == "publish_reel":
            from .dm_receipts import queue_receipt

            queue_receipt(db, db.get(Account, lane.account_id), parent, "succeeded")
        current.payload = {
            **current.payload,
            "music_status": music_status,
            "observed_at": clock().isoformat(),
            "media_id": observation["media_id"],
        }
