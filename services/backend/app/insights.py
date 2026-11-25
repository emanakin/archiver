"""Queue insight reads in the API; execute them only from the account worker."""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import settings
from .dashboard import fingerprint, receipt, remember, required
from .db import get_db
from .execution_errors import OperationBlocked, OwnershipLost
from .insight_schemas import InsightCreate, InsightObservation, InsightResponse
from .models import Account, ActionAttempt, AuditEvent, InsightCheck, Job, Relationship
from .schemas import Page, Strict

router = APIRouter(prefix="/api/v1")


class AudienceSummary(Strict):
    following: int
    requested: int
    mutual: int
    checked_following: int
    last_observation: datetime | None
    profile_followers: int | None = None
    profile_following: int | None = None
    profile_observed_at: datetime | None = None
    follower_change: int | None = None
    compared_at: datetime | None = None


@router.get("/accounts/{account_id}/audience", response_model=AudienceSummary)
def audience_summary(account_id: str, db: Session = Depends(get_db)):
    account = required(db, Account, account_id)
    following = Relationship.observed_state == "following"
    counts = db.execute(
        select(
            func.count().filter(following),
            func.count().filter(Relationship.observed_state == "requested"),
            func.count().filter(following, Relationship.follows_back.is_(True)),
            func.count().filter(following, Relationship.follows_back.is_not(None)),
            func.max(Relationship.last_checked_at),
        ).where(Relationship.account_id == account_id)
    ).one()
    profiles = (
        db.scalars(
            select(InsightCheck)
            .where(
                InsightCheck.account_id == account_id,
                InsightCheck.kind == "profile",
                InsightCheck.profile["id"].as_string() == account.instagram_id,
                InsightCheck.observed_at.is_not(None),
            )
            .order_by(InsightCheck.observed_at.desc(), InsightCheck.id.desc())
            .limit(2)
        ).all()
        if account.instagram_id
        else []
    )
    latest = profiles[0].profile if profiles else {}
    previous = profiles[1].profile if len(profiles) == 2 else {}
    change = (
        latest["followers"] - previous["followers"]
        if latest.get("followers") is not None and previous.get("followers") is not None
        else None
    )
    return AudienceSummary(
        profile_followers=latest.get("followers"),
        profile_following=latest.get("following"),
        profile_observed_at=profiles[0].observed_at if profiles else None,
        follower_change=change,
        compared_at=profiles[1].observed_at if change is not None else None,
        following=counts[0],
        requested=counts[1],
        mutual=counts[2],
        checked_following=counts[3],
        last_observation=counts[4],
    )


def response(db, check):
    job = db.get(Job, check.job_id)
    requests_used = db.scalar(
        select(func.coalesce(func.sum(ActionAttempt.request_count), 0)).where(
            ActionAttempt.job_id == job.id
        )
    )
    return InsightResponse(
        id=check.id,
        account_id=check.account_id,
        job_id=job.id,
        kind=check.kind,
        target=check.target,
        requested_limit=check.requested_limit,
        cursor=check.cursor,
        state=job.state,
        error_code=job.error,
        requests_used=requests_used,
        observed_at=check.observed_at,
        created_at=check.created_at,
        profile=check.profile,
        relationship=check.relationship,
        items=check.items,
        next_cursor=check.next_cursor,
        complete=check.complete,
        note=check.note or "Waiting for a real account observation.",
    )


@router.post("/insight-checks", status_code=202, response_model=InsightResponse)
def queue_check(body: InsightCreate, db: Session = Depends(get_db)):
    account = db.scalar(select(Account).where(Account.id == body.account_id).with_for_update())
    if not account:
        raise HTTPException(404, "Account not found")
    if account.mode != "live" or settings().adapter_mode == "mock":
        raise HTTPException(409, "Insights require a real Instagram connection")
    digest = fingerprint(body.model_dump(exclude={"idempotency_key"}))
    previous = receipt(db, account.id, body.idempotency_key, "insight", digest)
    if previous:
        return response(db, required(db, InsightCheck, previous.result["check_id"]))
    if account.session_state != "connected" or account.restricted:
        raise HTTPException(409, "Connect Instagram before requesting insights")
    check_id = str(uuid.uuid4())
    job = Job(
        account_id=account.id,
        kind="insight",
        idempotency_key=body.idempotency_key,
        payload={"check_id": check_id},
    )
    db.add(job)
    db.flush()
    check = InsightCheck(
        id=check_id,
        account_id=account.id,
        job_id=job.id,
        kind=body.kind,
        target=body.target,
        requested_limit=body.requested_limit,
        cursor=body.cursor,
    )
    db.add(check)
    remember(
        db,
        account.id,
        body.idempotency_key,
        "insight",
        digest,
        job_id=job.id,
        result={"check_id": check_id},
    )
    db.add(
        AuditEvent(
            account_id=account.id,
            event="insight_queued",
            detail={"check_id": check_id, "kind": body.kind},
        )
    )
    db.commit()
    return response(db, check)


@router.get("/insight-checks", response_model=Page[InsightResponse])
def list_checks(
    account_id: str,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    required(db, Account, account_id)
    query = select(InsightCheck).where(InsightCheck.account_id == account_id)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    items = db.scalars(
        query.order_by(InsightCheck.created_at.desc(), InsightCheck.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return {
        "items": [response(db, item) for item in items],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/insight-checks/{check_id}", response_model=InsightResponse)
def get_check(check_id: str, db: Session = Depends(get_db)):
    return response(db, required(db, InsightCheck, check_id))


def perform(engine, lane, job_id, adapter, clock):
    with Session(engine) as db:
        job = db.get(Job, job_id)
        check = db.get(InsightCheck, job.payload.get("check_id"))
        if not check or check.account_id != lane.account_id or check.job_id != job_id:
            raise OperationBlocked("Insight intent does not match this account job")
        account = db.get(Account, lane.account_id)
        arguments = (check.kind, check.target, check.requested_limit, check.cursor)
        known_user_id = (
            account.instagram_id if check.target.lower() == account.username.lower() else None
        )
    lane.assert_owned()
    observation = InsightObservation.model_validate(
        adapter.inspect(*arguments, known_user_id=known_user_id)
    )
    lane.assert_owned()
    at = clock()
    with Session(engine) as db, db.begin():
        db.scalar(select(Account).where(Account.id == lane.account_id).with_for_update())
        job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
        if job.claim_owner != lane.owner or job.state != "running":
            raise OwnershipLost("Insight job ownership changed during the read")
        check = db.get(InsightCheck, job.payload["check_id"])
        from .insight_watches import record

        record(db, job, observation, at)
        result = observation.model_dump(mode="json")
        for key in ("profile", "relationship", "items", "next_cursor", "complete", "note"):
            setattr(check, key, result[key])
        check.observed_at = at
