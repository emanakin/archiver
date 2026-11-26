"""Prepare replacements for explicitly selected published DM photo drafts."""

import argparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import engine
from app.models import Account, AuditEvent, ContentDraft, DmInbox, DmSubmission, Job


def prepare(db, account, original_id, retry_unmatched=False):
    original = db.get(ContentDraft, original_id)
    if (
        not original
        or original.account_id != account.id
        or original.status != "published"
        or original.kind not in {"feed_photo", "feed_carousel"}
        or original.discarded_at
        or not original.media_id
    ):
        raise ValueError("Expected an existing published photo from the archive account")
    publication = db.get(Job, original.publication_job_id)
    if not publication or publication.state != "succeeded":
        raise ValueError("The original publication must be verified")
    submission = db.scalar(select(DmSubmission).where(DmSubmission.draft_id == original.id))
    if not submission:
        raise ValueError("The original post must come from an approved DM submission")
    inbox = db.get(DmInbox, (account.id, submission.sender_id))
    if not inbox or not inbox.enabled or inbox.thread_id != submission.thread_id:
        raise ValueError("The original DM sender must still be approved")
    key = f"prepare-photo-replacement:{original.id}"
    previous = db.scalar(
        select(Job).where(Job.account_id == account.id, Job.idempotency_key == key)
    )
    if previous and not retry_unmatched:
        return previous.payload["replacement_draft_id"], previous.id
    music = ((submission.draft_commands or {}).get("commands") or {}).get("music")
    if music and music.get("value") != "playlist":
        raise ValueError("This share requested a specific soundtrack choice. Review it first.")
    if retry_unmatched:
        if not previous or previous.state != "blocked" or not previous.payload.get("music_checks"):
            raise ValueError("Expected a prior catalog check that did not find a usable match")
        draft = db.get(ContentDraft, previous.payload["replacement_draft_id"])
        if not draft or draft.status != "draft" or draft.playlist_music or draft.music_canonical_id:
            raise ValueError("The replacement already has music or has left Drafts")
        retry_key = key + ":retry"
        if retry := db.scalar(
            select(Job).where(Job.account_id == account.id, Job.idempotency_key == retry_key)
        ):
            return draft.id, retry.id
        payload = {
            name: value for name, value in previous.payload.items() if name != "music_checks"
        }
        payload.update(inbox_version=inbox.version, replacement_revision=draft.revision)
        retry = Job(
            account_id=account.id,
            kind="dm_replacement_music",
            idempotency_key=retry_key,
            payload=payload,
        )
        db.add(retry)
        db.flush()
        return draft.id, retry.id

    caption_job_id = (original.caption_review or {}).get("job_id")
    caption_job = db.get(Job, caption_job_id) if caption_job_id else None
    source = (caption_job.payload.get("source_caption") or "") if caption_job else ""
    draft = ContentDraft(
        account_id=account.id,
        kind=original.kind,
        asset_id=original.asset_id,
        photo_assets=original.photo_assets,
        photo_order=original.photo_order,
        fit_mode=original.fit_mode,
        rotation=original.rotation,
        overlay_text=original.overlay_text,
        text_position=original.text_position,
        source_music=original.source_music,
        caption=original.caption,
    )
    db.add(draft)
    db.flush()
    job = Job(
        account_id=account.id,
        kind="dm_replacement_music",
        idempotency_key=key,
        payload={
            "sender_id": inbox.sender_id,
            "inbox_version": inbox.version,
            "replacement_from": original.id,
            "replacement_source_submission_id": submission.id,
            "replacement_draft_id": draft.id,
            "replacement_revision": draft.revision,
            "replacement_caption_source": source,
        },
    )
    db.add(job)
    db.add(
        AuditEvent(
            account_id=account.id,
            event="photo_replacement_prepared",
            detail={"original_id": original.id, "replacement_id": draft.id},
        )
    )
    db.flush()
    return draft.id, job.id


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--retry-unmatched", action="store_true")
    parser.add_argument("original_id", nargs="+")
    args = parser.parse_args()
    with Session(engine) as db, db.begin():
        account = db.scalar(
            select(Account).where(Account.id == settings().dm_default_account_id).with_for_update()
        )
        if not account or account.paused or account.restricted or not account.read_only_verified_at:
            raise SystemExit("The archive account must be verified and active")
        results = [
            (original, *prepare(db, account, original, args.retry_unmatched))
            for original in args.original_id
        ]
    for original, draft, job in results:
        print(original, draft, job)


if __name__ == "__main__":
    main()
