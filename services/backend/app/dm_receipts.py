"""One-shot receipts sent to the archive's verified self conversation."""

import re
from urllib.parse import urlencode

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .dm_reader import RECEIPT_PREFIX, identity
from .execution_errors import OperationBlocked, OwnershipLost, PolicySkip, UncertainWrite
from .models import Account, AttemptEvent, ContentDraft, DmSubmission, Job, SavedAudio

RECEIPT = "dm_receipt"
PUBLICATIONS = {"publish_photo", "publish_carousel", "publish_story", "publish_reel"}
ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
DRAFT_HOLD_MESSAGES = {
    "default_held": "The follow-up inbox check could not confirm the latest commands.",
    "playlist_unavailable": "No unused playlist track matched in Instagram.",
    "source_music_unavailable": "The shared post has no reusable Instagram track.",
    "review_commands": "Check the publishing commands in Archive Desk.",
    "review_publication": "Review the publication in Archive Desk.",
}


def posted_url(account, draft):
    pk = identity(str(draft.media_id or "").split("_", 1)[0])
    if not pk:
        return None
    if draft.kind == "story_photo":
        return f"https://www.instagram.com/stories/{account.username}/{pk}/"
    code, value = "", int(pk)
    while value:
        value, digit = divmod(value, 64)
        code = ALPHABET[digit] + code
    path = "reel" if draft.kind == "feed_reel" else "p"
    return f"https://www.instagram.com/{path}/{code}/" if code else None


def receipts_enabled(account):
    config = settings()
    return bool(
        config.dm_self_receipts
        and account
        and config.dm_default_account_id == account.id
        and account.instagram_id
    )


def add_receipt(db, inbox, thread_id, origin, stage, title, location=None):
    if not inbox or not inbox.enabled or not inbox.direct_publish_from or not identity(thread_id):
        return
    key = f"dm-receipt:{origin.id}:{stage}"
    if db.scalar(
        select(Job.id).where(Job.account_id == origin.account_id, Job.idempotency_key == key)
    ):
        return
    text = f"{RECEIPT_PREFIX}{title}"
    if location:
        text += f"\n{location}"
    db.add(
        Job(
            account_id=origin.account_id,
            kind=RECEIPT,
            priority=2,
            idempotency_key=key,
            payload={
                "origin_job_id": origin.id,
                "sender_id": inbox.sender_id,
                "thread_id": thread_id,
                "inbox_version": inbox.version,
                "text": text,
            },
        )
    )


def queue_processing_receipt(db, inbox, thread_id, job):
    account = db.get(Account, inbox.account_id)
    if not receipts_enabled(account) or inbox.sender_id != account.instagram_id:
        return
    title = (
        "Received. Checking this sound."
        if job.kind == "dm_audio"
        else "Received. Processing your share."
    )
    add_receipt(db, inbox, thread_id, job, "received", title)


def queue_already_saved_audio(db, account, inbox, thread_id, job, message_id, audio):
    if not receipts_enabled(account) or inbox.sender_id != account.instagram_id:
        return
    status = {
        "feed_ready": "This sound is already saved and available for feed posts.",
        "feed_unmatched": "This sound is already saved. Instagram’s feed music catalog had no verified match.",
        "queued": "This sound is already saved. Its feed music check is pending.",
    }
    add_receipt(
        db,
        inbox,
        thread_id,
        job,
        f"already-saved:{message_id}",
        status.get(
            audio.state, "This sound is already saved. Its feed music check could not be completed."
        ),
        f"https://www.instagram.com/reels/audio/{audio.audio_id}/",
    )


def queue_receipt(db, account, job, state):
    from .dm_intake import sender_inbox

    if (
        not receipts_enabled(account)
        or job.kind not in PUBLICATIONS | {"dm_import", "dm_audio"}
        or state == "queued"
    ):
        return
    if job.kind == "dm_audio":
        inbox = sender_inbox(db, account.id, account.instagram_id)
        if (
            job.payload.get("sender_id") != account.instagram_id
            or not inbox
            or job.payload.get("inbox_version") != inbox.version
        ):
            return
        audio = db.get(SavedAudio, (account.id, job.payload.get("audio_id")))
        if not audio:
            return
        if state == "succeeded" and audio.state == "feed_ready":
            title = "Sound saved and available for feed posts."
        elif state == "succeeded" and audio.state == "feed_unmatched":
            title = "Sound saved. Instagram’s feed music catalog had no verified match."
        else:
            title = "Sound saved. The feed music check could not be completed."
        add_receipt(
            db,
            inbox,
            inbox.thread_id,
            job,
            state,
            title,
            f"https://www.instagram.com/reels/audio/{audio.audio_id}/",
        )
        return
    if job.kind == "dm_import":
        submission = db.get(DmSubmission, job.payload.get("submission_id"))
    else:
        submission = db.scalar(
            select(DmSubmission)
            .where(
                DmSubmission.account_id == account.id,
                DmSubmission.draft_id == job.payload.get("draft_id"),
            )
            .limit(1)
        )
    if (
        not submission
        or submission.account_id != account.id
        or submission.sender_id != account.instagram_id
    ):
        return
    inbox = sender_inbox(db, account.id, submission.sender_id)
    if (
        not inbox
        or not inbox.enabled
        or not inbox.direct_publish_from
        or inbox.thread_id != submission.thread_id
    ):
        return
    draft = db.get(ContentDraft, submission.draft_id) if submission.draft_id else None
    origin = settings().app_origin.rstrip("/")
    location = f"{origin}/queue?{urlencode({'account': account.id, 'tab': 'review'})}"
    if draft:
        location = f"{origin}/content?{urlencode({'account': account.id, 'draft': draft.id})}"
    if state == "succeeded" and job.kind == "dm_import":
        if job.payload.get("reel_url") and not draft:
            add_receipt(
                db,
                inbox,
                inbox.thread_id,
                job,
                state,
                (
                    "Reel source saved. Try preparing the video again or upload an MP4 in Archive Desk."
                    if job.payload.get("reel_import_note")
                    else "Reel source link saved."
                ),
                location,
            )
            return
        if not draft or draft.status != "draft":
            return
        command_status = (submission.draft_commands or {}).get("status")
        caption_status = (submission.caption_command or {}).get("state")
        title = "Reel saved as a video draft." if draft.kind == "feed_reel" else "Saved as a draft."
        if (draft.caption_review or {}).get("state") == "processing":
            title += " Caption analysis is in progress."
        if caption_status == "generation_failed":
            title += " Caption generation failed."
        if command_status == "review_publication" and caption_status == "generation_failed":
            command_status = None
        if reason := DRAFT_HOLD_MESSAGES.get(command_status):
            title += f" {reason}"
    elif state == "succeeded" and draft and draft.status == "published":
        title = "Posted!"
        location = posted_url(account, draft) or location
    elif state == "unknown":
        title = "Publishing result needs review."
    else:
        title = (
            "Could not import this share."
            if job.kind == "dm_import"
            else "Could not publish this draft."
        )
    add_receipt(db, inbox, inbox.thread_id, job, state, title, location)


def authorize_receipt(db, account, job, attempt, message, at):
    from .dm_intake import sender_inbox

    config = settings()
    if (
        job.kind != RECEIPT
        or not config.dm_self_receipts
        or config.dm_default_account_id != account.id
    ):
        raise OperationBlocked("This job cannot send a DM receipt")
    sender_id = job.payload.get("sender_id")
    inbox = sender_inbox(db, account.id, sender_id) if sender_id else None
    if (
        not inbox
        or sender_id != account.instagram_id
        or not inbox.enabled
        or not inbox.direct_publish_from
        or inbox.version != job.payload.get("inbox_version")
        or inbox.thread_id != job.payload.get("thread_id")
        or not identity(inbox.thread_id)
    ):
        raise PolicySkip("The self conversation no longer authorizes the receipt")
    text = job.payload["text"]
    if (
        message["thread_ids"] != [int(inbox.thread_id)]
        or message["text"] != text
        or message["links"] != re.findall(r"https?://[^\s]+", text)
        or not identity(message.get("context"))
    ):
        raise OperationBlocked("The DM request does not match the approved receipt")
    if attempt.dispatch_started_at:
        raise UncertainWrite("The DM receipt has already been dispatched")
    attempt.dispatch_started_at = job.dispatch_started_at = at
    job.payload = {**job.payload, "client_context": message["context"]}
    db.add(
        AttemptEvent(attempt_id=attempt.id, event="dispatch_started", detail={"operation": RECEIPT})
    )


def send_receipt(engine, lane, job_id, adapter, payload):
    result = adapter.client.direct_send(payload["text"], thread_ids=[payload["thread_id"]])
    message_id = identity(getattr(result, "id", None))
    if not message_id:
        raise UncertainWrite("Instagram did not confirm the DM receipt")
    lane.assert_owned()
    with Session(engine) as db, db.begin():
        job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
        if job.claim_owner != lane.owner:
            raise OwnershipLost("DM receipt claim changed")
        job.payload = {**job.payload, "receipt_message_id": message_id}


def receipt_identities(db, account_id):
    messages, contexts = set(), set()
    for payload in db.scalars(
        select(Job.payload)
        .where(Job.account_id == account_id, Job.kind == RECEIPT)
        .order_by(Job.created_at.desc())
        .limit(100)
    ):
        if message_id := payload.get("receipt_message_id"):
            messages.add(message_id)
        if context := payload.get("client_context"):
            contexts.add(context)
    return messages, contexts
