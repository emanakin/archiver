"""Local draft/image operations. These handlers never call Instagram."""

import re
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import Field, StrictInt, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from .catalog_music import source_feed_track
from .config import settings
from .content_images import carousel_frame, render_image, validate_image
from .content_storage import MAX_BYTES, StorageUnavailable, persist_asset, read_original
from .content_video import MAX_VIDEO_BYTES, validate_video
from .dashboard import fingerprint, receipt, remember, required
from .db import get_db
from .execution_errors import OperationBlocked
from .models import Account, AuditEvent, ContentAsset, ContentDraft, Job, now
from .schemas import ORMResponse, Page, Strict

router = APIRouter(prefix="/api/v1")
MAX_CAROUSEL_PHOTOS = 20


class AssetResponse(ORMResponse):
    id: str
    account_id: str
    mime: str
    byte_size: int
    width: int
    height: int
    created_at: datetime


class DraftValues(Strict):
    kind: Literal["feed_photo", "story_photo", "feed_carousel", "feed_reel"] = "feed_photo"
    caption: str = Field(default="", max_length=2200)
    music_canonical_id: str | None = Field(default=None, pattern=r"^[0-9]{1,32}$")
    fit_mode: Literal["fit", "fill", "blend", "native"] = "fit"
    rotation: Literal[0, 90, 180, 270] = 0
    overlay_text: str = Field(default="", max_length=180)
    text_position: Literal["top", "center", "bottom"] = "bottom"

    @model_validator(mode="after")
    def validate_music(self):
        if self.music_canonical_id and self.kind in {"story_photo", "feed_reel"}:
            raise ValueError("This draft type does not support feed catalog music")
        return self


class DraftCreate(DraftValues):
    fit_mode: Literal["fit", "fill", "blend", "native"] = "native"
    account_id: str
    asset_id: str
    idempotency_key: str = Field(min_length=8, max_length=200)


class DraftPatch(DraftValues):
    expected_revision: int = Field(ge=1)
    photo_order: list[StrictInt] | None = Field(default=None, max_length=MAX_CAROUSEL_PHOTOS)
    keep_playlist_music: bool = True
    use_source_music: bool = False


class StoryDraftCreate(Strict):
    expected_revision: int = Field(ge=1)


class DraftPhoto(Strict):
    number: int
    preview_url: str


class CatalogTrack(Strict):
    id: str | None
    title: str
    artist: str
    audio_asset_id: str | None = None
    audio_cluster_id: str | None = None
    start_ms: int | None = None


class PlaylistMusic(Strict):
    title: str
    artist: str


class DraftResponse(DraftValues):
    id: str
    account_id: str
    asset_id: str
    source_draft_id: str | None
    source_music: CatalogTrack | None = None
    playlist_music: PlaylistMusic | None = None
    revision: int
    status: str
    media_id: str | None
    publication_job_id: str | None
    created_at: datetime
    updated_at: datetime
    preview_url: str
    photos: list[DraftPhoto]
    reel_source: dict | None = None
    caption_review: dict | None = None


def preview_url(draft, number=None):
    path = f"/api/v1/content-drafts/{draft.id}/preview?revision={draft.revision}"
    return f"{path}&photo={number}" if number is not None else path


def view(draft):
    order = draft.photo_order or [1]
    return DraftResponse(
        **{
            key: getattr(draft, key)
            for key in DraftResponse.model_fields
            if key not in {"preview_url", "photos", "playlist_music"}
        },
        playlist_music=PlaylistMusic(
            title=draft.playlist_music["title"], artist=draft.playlist_music["artist"]
        )
        if draft.playlist_music
        else None,
        preview_url=preview_url(draft, order[0] if draft.photo_assets else None),
        photos=[
            DraftPhoto(number=n, preview_url=preview_url(draft, n))
            for n in order
            if draft.kind != "feed_reel"
        ],
    )


def is_photo_order(draft, order):
    return bool(draft.photo_assets) and sorted(order) == list(range(1, len(draft.photo_assets) + 1))


def visible_draft(db, draft_id):
    draft = required(db, ContentDraft, draft_id)
    if draft.discarded_at:
        raise HTTPException(404, "Draft was deleted")
    return draft


@router.post("/accounts/{account_id}/content-assets", status_code=201, response_model=AssetResponse)
async def upload_asset(account_id: str, request: Request, db: Session = Depends(get_db)):
    account = required(db, Account, account_id)
    if account.mode != "live":
        raise HTTPException(409, "Choose a real account for content drafts")
    db.rollback()
    is_video = request.headers.get("content-type", "").split(";")[0].strip() == "video/mp4"
    limit = MAX_VIDEO_BYTES if is_video else MAX_BYTES
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > limit:
            raise HTTPException(413, "MP4 limit is 32 MiB" if is_video else "Image limit is 8 MiB")
        data.extend(chunk)

    def save(data):
        try:
            metadata = validate_video(data) if is_video else validate_image(data)
            asset = persist_asset(db, settings(), account_id, data, metadata)
            result = AssetResponse.model_validate(asset)
            db.commit()
            return result
        except StorageUnavailable as error:
            raise HTTPException(503, str(error)) from None
        except OperationBlocked as error:
            raise HTTPException(422, str(error)) from None

    return await run_in_threadpool(save, bytes(data))


@router.post("/content-drafts", status_code=201, response_model=DraftResponse)
def create_draft(body: DraftCreate, db: Session = Depends(get_db)):
    account = db.scalar(select(Account).where(Account.id == body.account_id).with_for_update())
    if not account or account.mode != "live":
        raise HTTPException(404, "Account not found")
    if body.kind == "feed_carousel":
        raise HTTPException(422, "Import a multi-photo share to create a carousel draft")
    asset = required(db, ContentAsset, body.asset_id)
    if asset.account_id != account.id:
        raise HTTPException(404, "Asset not found for this account")
    if (body.kind == "feed_reel") != (asset.mime == "video/mp4"):
        raise HTTPException(422, "Choose an MP4 asset for a Reel and an image for a photo")
    digest = fingerprint(body.model_dump(exclude={"idempotency_key"}))
    previous = receipt(db, account.id, body.idempotency_key, "draft", digest)
    if previous:
        return view(visible_draft(db, previous.result["draft_id"]))
    draft = ContentDraft(**body.model_dump(exclude={"idempotency_key"}))
    db.add(draft)
    db.flush()
    if draft.kind == "feed_reel" and not draft.caption:
        from .caption_pipeline import enqueue_caption

        enqueue_caption(db, draft, "")
    remember(db, account.id, body.idempotency_key, "draft", digest, result={"draft_id": draft.id})
    db.add(
        AuditEvent(
            account_id=account.id, event="content_draft_created", detail={"draft_id": draft.id}
        )
    )
    db.commit()
    return view(draft)


@router.get("/content-drafts", response_model=Page[DraftResponse])
def list_drafts(
    account_id: str,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    required(db, Account, account_id)
    query = select(ContentDraft).where(
        ContentDraft.account_id == account_id, ContentDraft.discarded_at.is_(None)
    )
    return {
        "items": [
            view(draft)
            for draft in db.scalars(
                query.order_by(ContentDraft.updated_at.desc()).limit(limit).offset(offset)
            )
        ],
        "total": db.scalar(select(func.count()).select_from(query.subquery())),
        "limit": limit,
        "offset": offset,
    }


@router.get("/content-drafts/{draft_id}", response_model=DraftResponse)
def get_draft(draft_id: str, db: Session = Depends(get_db)):
    return view(visible_draft(db, draft_id))


@router.delete("/content-drafts/{draft_id}", status_code=204)
def discard_draft(draft_id: str, revision: int = Query(ge=1), db: Session = Depends(get_db)):
    existing = required(db, ContentDraft, draft_id)
    db.scalar(select(Account).where(Account.id == existing.account_id).with_for_update())
    draft = db.scalar(select(ContentDraft).where(ContentDraft.id == draft_id).with_for_update())
    if draft.discarded_at:
        return Response(status_code=204)
    if draft.revision != revision:
        raise HTTPException(409, "Draft changed; refresh before deleting it")
    if draft.status not in {"draft", "failed", "cancelled", "deleted"}:
        raise HTTPException(409, "Resolve or remove the publication before deleting this draft")
    if db.scalar(
        select(Job.id)
        .where(
            Job.account_id == draft.account_id,
            Job.payload["draft_id"].as_string() == draft.id,
            (Job.state.in_(("queued", "running")))
            | (Job.state.in_(("blocked", "unknown")) & Job.dispatch_started_at.is_not(None)),
        )
        .limit(1)
    ):
        raise HTTPException(409, "Resolve the remaining media action before deleting this draft")
    draft.discarded_at = now()
    db.add(
        AuditEvent(
            account_id=draft.account_id,
            event="content_draft_discarded",
            detail={"draft_id": draft.id, "revision": draft.revision},
        )
    )
    db.commit()
    return Response(status_code=204)


@router.post("/content-drafts/{draft_id}/story-draft", response_model=DraftResponse)
def create_story_draft(draft_id: str, body: StoryDraftCreate, db: Session = Depends(get_db)):
    existing = required(db, ContentDraft, draft_id)
    db.scalar(select(Account).where(Account.id == existing.account_id).with_for_update())
    source = db.scalar(select(ContentDraft).where(ContentDraft.id == draft_id).with_for_update())
    if (
        source.discarded_at
        or source.kind != "feed_photo"
        or source.status != "published"
        or not source.media_id
    ):
        raise HTTPException(409, "Choose a verified published photo post to make a story draft")
    story = db.scalar(select(ContentDraft).where(ContentDraft.source_draft_id == draft_id))
    if story:
        if story.discarded_at:
            raise HTTPException(409, "The story draft from this post was deleted")
        return view(story)
    if source.revision != body.expected_revision:
        raise HTTPException(409, "The photo post changed. Refresh it before creating a story draft")
    story = ContentDraft(
        account_id=source.account_id,
        asset_id=source.asset_id,
        source_draft_id=source.id,
        kind="story_photo",
        fit_mode="native" if source.fit_mode == "fit" else source.fit_mode,
        rotation=source.rotation,
        overlay_text=source.overlay_text,
        text_position=source.text_position,
    )
    db.add(story)
    db.flush()
    db.add(
        AuditEvent(
            account_id=source.account_id,
            event="content_story_draft_created",
            detail={"draft_id": story.id, "source_draft_id": source.id},
        )
    )
    db.commit()
    return view(story)


@router.patch("/content-drafts/{draft_id}", response_model=DraftResponse)
def edit_draft(draft_id: str, body: DraftPatch, db: Session = Depends(get_db)):
    existing = required(db, ContentDraft, draft_id)
    db.scalar(select(Account).where(Account.id == existing.account_id).with_for_update())
    draft = db.scalar(select(ContentDraft).where(ContentDraft.id == draft_id).with_for_update())
    if draft.discarded_at or draft.status != "draft":
        raise HTTPException(
            409, "This draft is approved or published; create a new draft to change its image"
        )
    if draft.revision != body.expected_revision:
        raise HTTPException(409, "Stale revision; refresh the draft before saving")
    if draft.source_draft_id and body.kind != "story_photo":
        raise HTTPException(409, "This draft is linked to a published photo post as a story")
    if (body.kind == "feed_carousel") != bool(draft.photo_assets):
        raise HTTPException(409, "Carousel drafts keep their original photo set")
    if body.kind != draft.kind and "feed_reel" in {body.kind, draft.kind}:
        raise HTTPException(409, "A Reel draft keeps its original video")
    if draft.kind == "feed_reel" and body.use_source_music:
        raise HTTPException(422, "Reels use the audio contained in the video")
    if body.caption != draft.caption and draft.caption_review:
        draft.caption_review = {**draft.caption_review, "state": "owner_caption", "reason": None}
    if body.photo_order is not None:
        if not is_photo_order(draft, body.photo_order):
            raise HTTPException(422, "Use every original photo number once")
        draft.photo_order = body.photo_order
    if body.use_source_music and body.kind == "story_photo":
        raise HTTPException(422, "Source music is available for feed posts")
    if not body.keep_playlist_music or body.music_canonical_id or body.kind == "story_photo":
        draft.playlist_music = None
    for key, value in body.model_dump(
        exclude={"expected_revision", "photo_order", "keep_playlist_music", "use_source_music"}
    ).items():
        setattr(draft, key, value)
    if body.use_source_music:
        original = draft.source_music or {}
        draft.playlist_music = source_feed_track(original)
        draft.music_canonical_id = None if draft.playlist_music else original.get("id")
        if not (draft.playlist_music or draft.music_canonical_id):
            raise HTTPException(422, "This draft has no reusable source music")
    draft.revision += 1
    draft.updated_at = now()
    db.commit()
    return view(draft)


@router.get("/content-drafts/{draft_id}/preview")
def preview(
    draft_id: str,
    revision: int = Query(ge=1),
    db: Session = Depends(get_db),
    photo: Annotated[int | None, Query(ge=1, le=MAX_CAROUSEL_PHOTOS)] = None,
    request: Request = None,
):
    draft = visible_draft(db, draft_id)
    if draft.revision != revision:
        raise HTTPException(409, "Draft changed; refresh the preview")
    if draft.photo_assets:
        number = photo if photo is not None else draft.photo_order[0]
        if number > len(draft.photo_assets):
            raise HTTPException(404, "This photo is not in the draft")
        asset_id = draft.photo_assets[number - 1]
    else:
        if photo not in {None, 1}:
            raise HTTPException(404, "This photo is not in the draft")
        asset_id = draft.asset_id
    asset = required(db, ContentAsset, asset_id)
    if asset.account_id != draft.account_id:
        raise HTTPException(404, "This photo is not in the draft")
    values = {key: getattr(draft, key) for key in DraftValues.model_fields}
    cover = None
    if draft.photo_assets and draft.fit_mode == "native":
        cover = required(db, ContentAsset, draft.photo_assets[draft.photo_order[0] - 1])
        if cover.account_id != draft.account_id:
            raise HTTPException(404, "This photo is not in the draft")
        if cover is not asset:
            db.expunge(cover)
    db.expunge(asset)
    db.rollback()
    try:
        config = settings()
        if asset.mime == "video/mp4":
            return video_preview(read_original(config, asset), request.headers.get("range") if request else None)
        frame = carousel_frame(config, cover, values) if cover is not None else None
        image = render_image(config, asset, values, frame)
    except StorageUnavailable as error:
        raise HTTPException(503, str(error)) from None
    except OperationBlocked as error:
        raise HTTPException(422, str(error)) from None
    return Response(
        image,
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


def video_preview(data, range_header):
    headers = {
        "Cache-Control": "no-store",
        "Accept-Ranges": "bytes",
        "X-Content-Type-Options": "nosniff",
    }
    if not range_header:
        return Response(data, media_type="video/mp4", headers=headers)
    match = re.fullmatch(r"bytes=(\d{0,12})-(\d{0,12})", range_header)
    if match and (match[1] or match[2]):
        if match[1]:
            start = int(match[1])
            end = min(int(match[2]), len(data) - 1) if match[2] else len(data) - 1
        else:
            start, end = max(0, len(data) - int(match[2])), len(data) - 1
        if 0 <= start <= end < len(data):
            headers["Content-Range"] = f"bytes {start}-{end}/{len(data)}"
            return Response(
                data[start : end + 1], status_code=206, media_type="video/mp4", headers=headers
            )
    return Response(status_code=416, headers={**headers, "Content-Range": f"bytes */{len(data)}"})
