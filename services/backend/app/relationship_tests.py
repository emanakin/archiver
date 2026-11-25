"""Explicit one-target test scope, independent of broad acquisition/cleanup."""

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .adapters.instagram import DiscoveredUser
from .config import settings
from .dashboard import account_settings, enqueue, required
from .db import get_db
from .execution_errors import OperationBlocked
from .live_collection import upsert_target
from .models import Account, AuditEvent, InsightCheck, Job, Relationship, RelationshipTest, now
from .schemas import JobCreate, ORMResponse, Page, Strict

router = APIRouter(prefix="/api/v1")


def aware(value):
    return value.replace(tzinfo=UTC) if value and value.tzinfo is None else value


class TestCreate(Strict):
    account_id: str
    check_id: str


class TestResponse(ORMResponse):
    id: str
    account_id: str
    target_id: str
    username: str
    before_check_id: str
    state: str
    follow_job_id: str | None
    unfollow_job_id: str | None
    error_code: str | None
    authorized_at: datetime | None
    expires_at: datetime | None
    completed_at: datetime | None
    created_at: datetime


def eligible_check(db, account, check_id):
    check = required(db, InsightCheck, check_id)
    job = db.get(Job, check.job_id)
    if (
        check.account_id != account.id
        or check.kind != "relationship"
        or job.state != "succeeded"
        or not check.observed_at
        or aware(check.observed_at) < now() - timedelta(hours=1)
    ):
        raise HTTPException(409, "Run a fresh relationship observation before preparing this test")
    relationship = check.relationship or {}
    profile = check.profile or {}
    target_id = relationship.get("target_id")
    if not target_id or target_id == account.instagram_id or profile.get("id") != target_id:
        raise HTTPException(409, "This observation does not identify a different account")
    if relationship.get("state") != "none" or relationship.get("follows_back") is not False:
        raise HTTPException(
            409,
            "A fresh test requires an account you do not follow and that does not follow you back",
        )
    if profile.get("is_private") is not False:
        raise HTTPException(
            409, "Use a public test account; pending requests are handled separately"
        )
    existing = db.scalar(
        select(Relationship)
        .where(Relationship.account_id == account.id, Relationship.target_id == target_id)
        .with_for_update()
    )
    if existing and (
        existing.protected
        or existing.excluded
        or existing.ever_followed
        or existing.observed_state in {"following", "requested"}
    ):
        raise HTTPException(
            409,
            "Protected, excluded, existing or previously followed accounts cannot be test targets",
        )
    return check, target_id, profile["username"], existing


@router.post("/relationship-tests", status_code=201, response_model=TestResponse)
def prepare(body: TestCreate, db: Session = Depends(get_db)):
    account = db.scalar(select(Account).where(Account.id == body.account_id).with_for_update())
    if (
        not account
        or account.mode != "live"
        or account.session_state != "connected"
        or account.restricted
    ):
        raise HTTPException(409, "A connected Instagram account is required")
    check, target_id, username, _ = eligible_check(db, account, body.check_id)
    previous = db.scalar(
        select(RelationshipTest).where(
            RelationshipTest.account_id == account.id, RelationshipTest.target_id == target_id
        )
    )
    if previous:
        return previous
    upsert_target(db, DiscoveredUser(target_id, username), now())
    case = RelationshipTest(
        account_id=account.id, target_id=target_id, username=username, before_check_id=check.id
    )
    db.add(case)
    db.add(
        AuditEvent(
            account_id=account.id,
            event="relationship_test_prepared",
            detail={"target_id": target_id},
        )
    )
    db.commit()
    return case


@router.get("/relationship-tests", response_model=Page[TestResponse])
def list_tests(account_id: str, db: Session = Depends(get_db)):
    required(db, Account, account_id)
    rows = db.scalars(
        select(RelationshipTest)
        .where(RelationshipTest.account_id == account_id)
        .order_by(RelationshipTest.created_at.desc())
        .limit(100)
    ).all()
    return {"items": rows, "total": len(rows), "limit": 100, "offset": 0}


@router.post("/relationship-tests/{test_id}/approve", response_model=TestResponse)
def approve(test_id: str, db: Session = Depends(get_db)):
    existing = required(db, RelationshipTest, test_id)
    account = db.scalar(select(Account).where(Account.id == existing.account_id).with_for_update())
    case = db.scalar(
        select(RelationshipTest).where(RelationshipTest.id == test_id).with_for_update()
    )
    if case.authorized_at:
        return case
    if settings().adapter_mode != "live":
        raise HTTPException(
            409,
            "Write transport is paused; this test is prepared but cannot send actions in read-only mode",
        )
    if (
        account.session_state != "connected"
        or account.restricted
        or not account.read_only_verified_at
    ):
        raise HTTPException(409, "A validated connected session is required")
    if account.live_acquisition_enabled or account.live_cleanup_enabled:
        raise HTTPException(409, "Pause broad action workflows before running an isolated test")
    config = account_settings(db, account.id)
    if config["follows_per_day"] == 0 or config["unfollows_per_day"] < 1:
        raise HTTPException(
            409, "Set an allowance of at least one follow and one unfollow for this explicit test"
        )
    check, _, _, relationship = eligible_check(db, account, case.before_check_id)
    if relationship is None:
        relationship = Relationship(account_id=account.id, target_id=case.target_id)
        db.add(relationship)
    relationship.observed_state, relationship.follows_back = "none", False
    relationship.last_checked_at = relationship.follows_back_observed_at = check.observed_at
    db.flush()
    job, duplicate = enqueue(
        db,
        JobCreate(
            account_id=account.id,
            target_id=case.target_id,
            kind="follow",
            idempotency_key=f"relationship-test:{case.id}:follow",
        ),
    )
    if duplicate:
        raise HTTPException(409, "A follow intent already exists; review the ledger")
    job.payload = {"relationship_test_id": case.id}
    case.follow_job_id, case.state, case.authorized_at = job.id, "follow_pending", now()
    case.expires_at = now() + timedelta(minutes=10)
    db.add(
        AuditEvent(
            account_id=account.id,
            event="relationship_test_approved",
            detail={
                "test_id": case.id,
                "target_id": case.target_id,
                "scope": "one_follow_then_one_unfollow",
            },
        )
    )
    db.commit()
    return case


def approved_case(db, job, at):
    test_id = job.payload.get("relationship_test_id")
    if not test_id:
        return None
    case = db.get(RelationshipTest, test_id)
    valid = (
        case
        and case.account_id == job.account_id
        and case.target_id == job.target_id
        and case.authorized_at
        and case.expires_at
        and aware(case.expires_at) > at
    )
    valid = valid and (
        (job.kind == "follow" and case.follow_job_id == job.id and case.state == "follow_pending")
        or (
            job.kind == "unfollow"
            and case.unfollow_job_id == job.id
            and case.state == "unfollow_pending"
        )
    )
    if not valid:
        raise OperationBlocked(
            "Relationship test approval is missing, expired or does not match this job"
        )
    return case


def advance(db, job, state, at):
    if not job.payload.get("relationship_test_id"):
        return
    case = db.scalar(
        select(RelationshipTest)
        .where(RelationshipTest.id == job.payload["relationship_test_id"])
        .with_for_update()
    )
    if (
        not case
        or case.account_id != job.account_id
        or case.target_id != job.target_id
        or job.id not in {case.follow_job_id, case.unfollow_job_id}
    ):
        return
    if state != "succeeded":
        case.state = "unknown" if state == "unknown" else "review_required"
        case.error_code = (job.error or state)[:64]
        return
    if job.kind == "follow":
        relationship = db.scalar(
            select(Relationship).where(
                Relationship.account_id == case.account_id, Relationship.target_id == case.target_id
            )
        )
        if (
            not relationship
            or relationship.observed_state != "following"
            or not relationship.managed
        ):
            case.state, case.error_code = "review_required", "FOLLOW_NOT_CONFIRMED"
            return
        cleanup = Job(
            account_id=case.account_id,
            target_id=case.target_id,
            kind="unfollow",
            scheduled_at=at,
            priority=1,
            idempotency_key=f"relationship-test:{case.id}:unfollow",
            payload={"relationship_test_id": case.id},
        )
        db.add(cleanup)
        db.flush()
        case.unfollow_job_id, case.state = cleanup.id, "unfollow_pending"
    elif job.kind == "unfollow":
        case.state, case.completed_at, case.error_code = "restored", at, None
