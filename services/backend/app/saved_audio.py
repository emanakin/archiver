"""Store Instagram audio references and confirm eligible feed catalog matches."""

from datetime import datetime
from random import choice

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from .dashboard import required
from .db import get_db
from .execution_errors import OwnershipLost
from .models import Account, Job, PlaylistSelection, SavedAudio
from .music_context import named_track
from .schemas import Strict
from .spotify_playlist import spotify_query

router = APIRouter(prefix="/api/v1")


class SavedAudioResponse(Strict):
    audio_id: str
    title: str
    artist: str
    state: str
    created_at: datetime
    used_at: datetime | None


@router.get("/accounts/{account_id}/saved-audio", response_model=list[SavedAudioResponse])
def list_audio(account_id: str, db: Session = Depends(get_db)):
    required(db, Account, account_id)
    rows = db.scalars(
        select(SavedAudio)
        .where(SavedAudio.account_id == account_id)
        .order_by(SavedAudio.created_at.desc(), SavedAudio.audio_id)
        .limit(50)
    )
    return [SavedAudioResponse.model_validate(row, from_attributes=True) for row in rows]


def record_audio(db, inbox, message, at, thread_id=None):
    from .dm_receipts import queue_already_saved_audio, queue_processing_receipt

    account = db.get(Account, inbox.account_id)
    if (
        not account
        or message.sender_id != account.instagram_id
        or inbox.sender_id != account.instagram_id
    ):
        return
    for track in message.audio:
        key = f"dm-audio:{track.id}"
        if audio := db.get(SavedAudio, (account.id, track.id)):
            if thread_id:
                origin = db.scalar(
                    select(Job).where(Job.account_id == account.id, Job.idempotency_key == key)
                )
                if origin:
                    queue_already_saved_audio(
                        db, account, inbox, thread_id, origin, message.id, audio
                    )
            continue
        db.add(
            SavedAudio(
                account_id=account.id,
                audio_id=track.id,
                title=track.title,
                artist=track.artist,
                created_at=at,
            )
        )
        job = Job(
            account_id=account.id,
            kind="dm_audio",
            idempotency_key=key,
            payload={
                "audio_id": track.id,
                "sender_id": inbox.sender_id,
                "inbox_version": inbox.version,
            },
        )
        db.add(job)
        db.flush()
        if thread_id:
            queue_processing_receipt(db, inbox, thread_id, job)


def audio_candidate(db, account_id):
    rows = db.scalars(
        select(SavedAudio).where(
            SavedAudio.account_id == account_id,
            SavedAudio.state == "feed_ready",
        )
    ).all()
    spent = set(
        db.scalars(
            select(PlaylistSelection.audio_asset_id).where(
                PlaylistSelection.account_id == account_id,
            )
        )
    )
    spent.update(row.feed_music["audio_asset_id"] for row in rows if row.used_at and row.feed_music)
    available = [
        row for row in rows if row.feed_music and row.feed_music["audio_asset_id"] not in spent
    ]
    if not available:
        return None
    row = choice(available)
    return row.feed_music, dict(
        audio_id=row.audio_id, title=row.title, artist=row.artist, state="saved_audio_matched"
    )


def spent_audio_assets(db, account_id):
    values = db.scalars(
        select(SavedAudio.feed_music).where(
            SavedAudio.account_id == account_id,
            SavedAudio.used_at.is_not(None),
        )
    )
    return {track["audio_asset_id"] for track in values if track}


def mark_used(db, account_id, native, at):
    if not native:
        return
    rows = db.scalars(
        select(SavedAudio).where(
            SavedAudio.account_id == account_id,
            SavedAudio.state == "feed_ready",
            SavedAudio.used_at.is_(None),
        )
    )
    for row in rows:
        if row.feed_music and row.feed_music["audio_asset_id"] == native["audio_asset_id"]:
            row.used_at = at


def verify_audio(engine, lane, job_id, adapter, guard):
    from .dm_intake import claim, enabled_inbox

    with Session(engine) as db:
        job = db.get(Job, job_id)
        row = db.get(SavedAudio, (lane.account_id, job.payload["audio_id"]))
        if not row or row.state != "queued":
            return
        title, artist, audio_id = row.title, row.artist, row.audio_id
    native = None
    outcome = "no_match"
    if artist:
        query = spotify_query(title, artist)
        guard.music_queries = {query}
        native, outcome = named_track(adapter, title, query, artist, allow_partial=False)
    with Session(engine) as db, db.begin():
        job = claim(db, lane, job_id)
        inbox = enabled_inbox(db, job)
        if inbox.sender_id != db.get(Account, lane.account_id).instagram_id:
            raise OwnershipLost("Audio lookup no longer belongs to the self conversation")
        row = db.get(SavedAudio, (lane.account_id, audio_id))
        if row and row.state == "queued":
            row.feed_music = native
            row.state = (
                "feed_ready"
                if native
                else "search_failed"
                if outcome == "search_failed"
                else "feed_unmatched"
            )
