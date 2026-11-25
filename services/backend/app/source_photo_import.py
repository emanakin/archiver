"""Import a selected photo from a saved post observation as a local draft."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .content_images import validate_image
from .content_storage import persist_asset, upload_backend
from .dashboard import required
from .db import get_db
from .dm_intake import claim
from .dm_reader import POST_URL, read_photo
from .models import Account, AuditEvent, ContentDraft, InsightCheck, Job
from .schemas import JobResponse, Strict

router = APIRouter(prefix="/api/v1")


class PhotoSelection(Strict):
    item_id: str = Field(pattern=r"^[0-9]{1,64}$")


@router.post("/insight-checks/{check_id}/import-photo", status_code=202, response_model=JobResponse)
def queue_photo_import(check_id: str, body: PhotoSelection, db: Session = Depends(get_db)):
    check = required(db, InsightCheck, check_id)
    account = db.scalar(select(Account).where(Account.id == check.account_id).with_for_update())
    if (
        not account
        or account.mode != "live"
        or account.session_state != "connected"
        or account.restricted
        or settings().adapter_mode == "mock"
    ):
        raise HTTPException(409, "Connect Instagram before importing a source photo")
    item = next((item for item in check.items if item.get("id") == body.item_id), None)
    if (
        check.kind != "posts"
        or not check.observed_at
        or not item
        or item.get("media_type") != "Photo"
        or not POST_URL.fullmatch(item.get("url") or "")
    ):
        raise HTTPException(409, "Select a single photo from a saved recent-post observation")
    previous = db.scalar(
        select(Job)
        .where(
            Job.account_id == account.id,
            Job.kind == "import_source_photo",
            Job.payload["media_ref"].as_string() == item["url"],
            Job.state.in_(("queued", "running", "succeeded")),
        )
        .limit(1)
    )
    if previous:
        return previous
    from .models import uid

    job = Job(
        account_id=account.id,
        kind="import_source_photo",
        idempotency_key="source-photo:" + uid(),
        payload={"check_id": check.id, "media_ref": item["url"]},
    )
    db.add(job)
    db.commit()
    return job


def perform(engine, lane, job_id, adapter, guard):
    with Session(engine) as db:
        job = db.get(Job, job_id)
        if job.payload.get("draft_id"):
            return
        reference = job.payload["media_ref"]
    config = settings()
    upload_backend(config)
    raw, caption, source_music = read_photo(adapter.client, reference, guard)
    metadata = validate_image(raw)
    with Session(engine) as db:
        asset_id = persist_asset(db, config, lane.account_id, raw, metadata).id
        job = claim(db, lane, job_id)
        draft = ContentDraft(
            account_id=lane.account_id,
            asset_id=asset_id,
            caption=caption,
            source_music=source_music,
        )
        db.add(draft)
        db.flush()
        job.payload = {**job.payload, "draft_id": draft.id}
        db.add(
            AuditEvent(
                account_id=lane.account_id,
                event="source_photo_draft_created",
                detail={
                    "draft_id": draft.id,
                    "check_id": job.payload["check_id"],
                    "job_id": job.id,
                },
            )
        )
        db.commit()
