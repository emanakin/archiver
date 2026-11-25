"""Budgeted polling, never an asserted complete or real-time like event feed."""

import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .account_lane import AccountLane
from .dashboard import required
from .db import get_db
from .execution_errors import PolicySkip
from .models import Account, AuditEvent, InsightCheck, InsightWatch, Job, LikerObservation, now
from .schemas import ORMResponse, Page, SourceCreate, Strict

router = APIRouter(prefix="/api/v1")


class WatchCreate(Strict):
    account_id: str
    target: str = Field(min_length=1, max_length=512)
    interval_minutes: int = Field(default=60, ge=5, le=1440)
    sample_size: int = Field(default=5, ge=1, le=20)

    @model_validator(mode="after")
    def normalize(self):
        self.target = SourceCreate(
            account_id=self.account_id, kind="post_likers", value=self.target
        ).value
        return self


class WatchPatch(Strict):
    expected_version: int = Field(ge=1)
    enabled: bool
    interval_minutes: int = Field(ge=5, le=1440)
    sample_size: int = Field(ge=1, le=20)


class WatchResponse(ORMResponse):
    id: str
    account_id: str
    target: str
    enabled: bool
    interval_minutes: int
    sample_size: int
    version: int
    next_due_at: datetime
    first_success_at: datetime | None
    last_success_at: datetime | None
    created_at: datetime


class LikerResponse(ORMResponse):
    target_id: str
    username: str
    first_seen_at: datetime
    last_seen_at: datetime
    baseline: bool


@router.post("/insight-watches", status_code=201, response_model=WatchResponse)
def create_watch(body: WatchCreate, db: Session = Depends(get_db)):
    account = db.scalar(select(Account).where(Account.id == body.account_id).with_for_update())
    if not account:
        raise HTTPException(404, "Account not found")
    if account.mode != "live":
        raise HTTPException(409, "Post observations require a real account")
    existing = db.scalar(
        select(InsightWatch).where(
            InsightWatch.account_id == account.id, InsightWatch.target == body.target
        )
    )
    if existing:
        return existing
    watch = InsightWatch(**body.model_dump(), enabled=False)
    db.add(watch)
    db.add(
        AuditEvent(
            account_id=account.id, event="liker_watch_created", detail={"target": body.target}
        )
    )
    db.commit()
    return watch


@router.get("/insight-watches", response_model=Page[WatchResponse])
def list_watches(
    account_id: str,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    required(db, Account, account_id)
    query = select(InsightWatch).where(InsightWatch.account_id == account_id)
    return {
        "items": db.scalars(
            query.order_by(InsightWatch.created_at.desc()).limit(limit).offset(offset)
        ).all(),
        "total": db.scalar(select(func.count()).select_from(query.subquery())),
        "limit": limit,
        "offset": offset,
    }


@router.patch("/insight-watches/{watch_id}", response_model=WatchResponse)
def update_watch(watch_id: str, body: WatchPatch, db: Session = Depends(get_db)):
    existing = required(db, InsightWatch, watch_id)
    account = db.scalar(select(Account).where(Account.id == existing.account_id).with_for_update())
    watch = db.scalar(select(InsightWatch).where(InsightWatch.id == watch_id).with_for_update())
    if body.expected_version != watch.version:
        raise HTTPException(409, "Stale version; refresh the post watch")
    if body.enabled and (account.session_state != "connected" or account.restricted):
        raise HTTPException(409, "Connect Instagram before enabling polling")
    watch.enabled, watch.interval_minutes, watch.sample_size = (
        body.enabled,
        body.interval_minutes,
        body.sample_size,
    )
    watch.version += 1
    watch.next_due_at = now()
    db.add(
        AuditEvent(
            account_id=account.id,
            event="liker_watch_changed",
            detail={"watch_id": watch.id, "enabled": watch.enabled},
        )
    )
    db.commit()
    return watch


@router.get("/insight-watches/{watch_id}/observations", response_model=Page[LikerResponse])
def observations(
    watch_id: str,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    required(db, InsightWatch, watch_id)
    query = select(LikerObservation).where(LikerObservation.watch_id == watch_id)
    return {
        "items": db.scalars(
            query.order_by(LikerObservation.first_seen_at.desc(), LikerObservation.target_id)
            .limit(limit)
            .offset(offset)
        ).all(),
        "total": db.scalar(select(func.count()).select_from(query.subquery())),
        "limit": limit,
        "offset": offset,
    }


def materialize(engine, clock=now):
    at = clock()
    with Session(engine) as db:
        account_ids = db.scalars(
            select(InsightWatch.account_id)
            .where(InsightWatch.enabled.is_(True), InsightWatch.next_due_at <= at)
            .distinct()
        ).all()
    for account_id in account_ids:
        with AccountLane(engine, account_id) as lane:
            if not lane.acquired:
                continue
            with Session(engine) as db, db.begin():
                account = db.scalar(
                    select(Account).where(Account.id == account_id).with_for_update()
                )
                if (
                    account.mode != "live"
                    or account.paused
                    or account.restricted
                    or account.session_state != "connected"
                ):
                    continue
                for watch in db.scalars(
                    select(InsightWatch)
                    .where(
                        InsightWatch.account_id == account_id,
                        InsightWatch.enabled.is_(True),
                        InsightWatch.next_due_at <= at,
                    )
                    .with_for_update()
                ):
                    active = db.scalar(
                        select(Job.id)
                        .where(
                            Job.account_id == account_id,
                            Job.kind == "insight",
                            Job.state.in_(["queued", "running"]),
                            Job.payload["watch_id"].as_string() == watch.id,
                        )
                        .limit(1)
                    )
                    if active:
                        continue
                    check_id = str(uuid.uuid4())
                    job = Job(
                        account_id=account_id,
                        kind="insight",
                        scheduled_at=at,
                        idempotency_key=f"liker-watch:{watch.id}:{watch.version}:{watch.next_due_at.isoformat()}",
                        payload={
                            "check_id": check_id,
                            "watch_id": watch.id,
                            "watch_version": watch.version,
                        },
                    )
                    db.add(job)
                    db.flush()
                    db.add(
                        InsightCheck(
                            id=check_id,
                            account_id=account_id,
                            job_id=job.id,
                            kind="likers",
                            target=watch.target,
                            requested_limit=watch.sample_size,
                        )
                    )
                    watch.next_due_at = at + timedelta(minutes=watch.interval_minutes)


def check_enabled(db, job):
    if job.payload.get("watch_id"):
        watch = db.get(InsightWatch, job.payload["watch_id"])
        if (
            not watch
            or not watch.enabled
            or watch.account_id != job.account_id
            or watch.version != job.payload.get("watch_version")
        ):
            raise PolicySkip("Post polling was paused or its settings changed")


def record(db, job, observation, at):
    check_enabled(db, job)
    if not job.payload.get("watch_id"):
        return
    watch = db.scalar(
        select(InsightWatch).where(InsightWatch.id == job.payload["watch_id"]).with_for_update()
    )
    baseline = watch.first_success_at is None
    for item in observation.items:
        row = db.get(LikerObservation, (watch.id, item.id))
        if row is None:
            db.add(
                LikerObservation(
                    watch_id=watch.id,
                    target_id=item.id,
                    username=item.username,
                    first_seen_at=at,
                    last_seen_at=at,
                    baseline=baseline,
                )
            )
        else:
            row.username, row.last_seen_at = item.username, at
    watch.first_success_at = watch.first_success_at or at
    watch.last_success_at = at
