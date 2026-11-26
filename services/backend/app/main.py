import secrets
import uuid
from datetime import UTC, date, datetime, time, timedelta
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, aliased
from starlette.concurrency import run_in_threadpool

from .caption_pipeline import router as caption_router
from .config import settings
from .content_drafts import router as content_router
from .content_publication import router as publication_router
from .dashboard import (
    account_settings,
    activity_query,
    activity_views,
    candidate_query,
    candidate_views,
    enqueue,
    fingerprint,
    receipt,
    relationship_view,
    remember,
    required,
    row,
    source_view,
)
from .db import SessionLocal, get_db
from .dm_intake import poll_jobs, sender_inbox
from .dm_intake import router as dm_router
from .google_signin import SESSION, current_principal, validate_runtime
from .google_signin import router as auth_router
from .insight_watches import router as watches_router
from .insights import router as insights_router
from .models import (
    Account,
    AccountSession,
    AccountSettings,
    Action,
    ActionAttempt,
    AttemptEvent,
    AuditEvent,
    BudgetUsage,
    FollowIntent,
    Job,
    Relationship,
    Source,
    Target,
    WorkerHeartbeat,
    now,
)
from .onboarding_routes import router as onboarding_router
from .relationship_tests import router as relationship_tests_router
from .saved_audio import router as saved_audio_router
from .schemas import (
    AccountResponse,
    ActionResponse,
    ActivityResponse,
    AttemptResponse,
    BulkJobs,
    BulkResult,
    CandidateResponse,
    ConnectionResponse,
    Intent,
    JobCreate,
    JobResponse,
    OverviewResponse,
    Page,
    PausePatch,
    RelationshipPatch,
    RelationshipResponse,
    SettingsPatch,
    SettingsResponse,
    SourceCreate,
    SourcePatch,
    SourceResponse,
    SourceSchedulePatch,
    WorkerResponse,
)
from .source_photo_import import router as source_photo_router
from .spotify_playlist import router as playlist_router
from .timing import next_window, scheduled_instant, within_window
from .workflow_routes import router as workflow_router

app = FastAPI(
    title="Archive Desk", version="0.2.0", docs_url=None, redoc_url=None, openapi_url=None
)
app.include_router(workflow_router)
app.include_router(onboarding_router)
app.include_router(insights_router)
app.include_router(watches_router)
app.include_router(relationship_tests_router)
app.include_router(content_router)
app.include_router(publication_router)
app.include_router(dm_router)
app.include_router(caption_router)
app.include_router(playlist_router)
app.include_router(saved_audio_router)
app.include_router(source_photo_router)
app.include_router(auth_router)


def error_response(request, status, code, message, details=None):
    return JSONResponse(
        {
            "detail": message,
            "error": {
                "code": code,
                "message": message,
                "retryable": status == 503,
                "request_id": getattr(request.state, "request_id", ""),
                "details": details,
            },
        },
        status_code=status,
        headers={"Cache-Control": "no-store"},
    )


@app.middleware("http")
async def authentication(request: Request, call_next):
    request.state.request_id = str(uuid.uuid4())
    cfg = settings()
    if request.url.path.startswith("/api/"):
        if not secrets.compare_digest(
            request.headers.get("x-internal-token", "").encode(), cfg.internal_api_token.encode()
        ):
            return error_response(request, 401, "UNAUTHORIZED", "Unauthorized")
        if cfg.auth_mode == "google":
            try:
                principal = await run_in_threadpool(current_principal, request.cookies.get(SESSION))
            except (RuntimeError, SQLAlchemyError):
                return error_response(request, 503, "UNAVAILABLE", "Sign-in is temporarily unavailable")
            if principal is None:
                return error_response(request, 401, "UNAUTHORIZED", "Sign in with Google")
            request.state.principal = principal
        if (
            request.method not in {"GET", "HEAD", "OPTIONS"}
            and request.headers.get("origin") != cfg.app_origin
        ):
            return error_response(request, 403, "ORIGIN_REJECTED", "Origin rejected")
    response = await call_next(request)
    response.headers["X-Request-ID"] = request.state.request_id
    if request.url.path.startswith(("/api/", "/auth/")):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(HTTPException)
async def domain_error(request, exc):
    code = {
        401: "UNAUTHORIZED",
        403: "ACCESS_DENIED",
        404: "NOT_FOUND",
        409: "STATE_CONFLICT",
        503: "UNAVAILABLE",
    }.get(exc.status_code, "INVALID_REQUEST")
    if "Stale version" in str(exc.detail):
        code = "VERSION_CONFLICT"
    return error_response(request, exc.status_code, code, str(exc.detail))


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    details = [{"loc": list(x["loc"]), "message": x["msg"]} for x in exc.errors()]
    return error_response(request, 422, "VALIDATION_ERROR", "Check the submitted fields", details)


@app.exception_handler(IntegrityError)
async def integrity_failure(request, exc):
    if getattr(getattr(exc.orig, "diag", None), "constraint_name", None) == "workspace_queue_limit":
        return error_response(request, 429, "QUEUE_FULL", "Wait for your workspace queue to finish")
    return error_response(request, 409, "STATE_CONFLICT", "This change conflicts with saved data")


def page(items, limit, offset):
    return {
        "items": items[offset : offset + limit],
        "total": len(items),
        "limit": limit,
        "offset": offset,
    }


@app.get("/health/live")
def live():
    return {"status": "ok"}


@app.get("/health/ready")
def ready():
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
            current = MigrationContext.configure(db.connection()).get_current_revision()
            if current != expected_schema():
                raise HTTPException(503, "Database migrations are required")
            if settings().auth_mode == "google":
                validate_runtime(db.connection())
    except (RuntimeError, ValueError, KeyError, OSError, SQLAlchemyError):
        raise HTTPException(503, "Database unavailable") from None
    return {"status": "ready", "mode": settings().adapter_mode, "schema_revision": current}


@lru_cache
def expected_schema():
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    return ScriptDirectory.from_config(config).get_current_head()


@app.get("/api/v1/openapi.json", include_in_schema=False)
def schema():
    return app.openapi()


@app.get("/api/v1/accounts", response_model=Page[AccountResponse])
def accounts(mode: str | None = None, db: Session = Depends(get_db)):
    query = select(Account).order_by(Account.created_at, Account.id)
    if mode:
        if mode not in {"live", "mock"}:
            raise HTTPException(422, "Invalid account mode")
        query = query.where(Account.mode == mode)
    return page(
        [row(x) for x in db.scalars(query)],
        100,
        0,
    )


@app.patch("/api/v1/accounts/{account_id}/pause", response_model=AccountResponse)
def pause(account_id: str, body: PausePatch, db: Session = Depends(get_db)):
    account = required(db, Account, account_id)
    account.paused = body.paused
    db.add(AuditEvent(account_id=account_id, event="pause_changed", detail={"paused": body.paused}))
    db.commit()
    return row(account)


@app.get("/api/v1/overview", response_model=OverviewResponse)
def overview(account_id: str, db: Session = Depends(get_db)):
    account = required(db, Account, account_id)
    counts = dict(
        db.execute(
            select(Job.state, func.count()).where(Job.account_id == account_id).group_by(Job.state)
        ).all()
    )
    available_count = db.scalar(
        select(func.count()).select_from(
            candidate_query(account_id, available="eligible").order_by(None).subquery()
        )
    )
    protected_count = db.scalar(
        select(func.count())
        .select_from(Relationship)
        .where(Relationship.account_id == account_id, Relationship.protected.is_(True))
    )
    due_count = db.scalar(
        select(func.count())
        .select_from(Relationship)
        .where(
            Relationship.account_id == account_id,
            Relationship.managed.is_(True),
            Relationship.observed_state == "following",
            Relationship.protected.is_(False),
            Relationship.excluded.is_(False),
            Relationship.unfollow_due_at <= now(),
        )
    )

    next_job = db.scalar(
        select(Job)
        .where(Job.account_id == account_id, Job.state == "queued")
        .order_by(Job.scheduled_at, Job.id)
        .limit(1)
    )
    queued_follows = db.scalar(
        select(func.count())
        .select_from(Job)
        .where(Job.account_id == account_id, Job.kind == "follow", Job.state == "queued")
    )
    day = (
        now().astimezone(ZoneInfo(account_settings(db, account_id)["timezone"])).date().isoformat()
    )
    usage = {
        item.category: item.consumed
        for item in db.scalars(
            select(BudgetUsage).where(
                BudgetUsage.account_id == account_id, BudgetUsage.local_day == day
            )
        )
    }
    at = now()
    oldest = db.scalar(
        select(func.min(Job.scheduled_at)).where(
            Job.account_id == account_id, Job.state == "queued", Job.scheduled_at <= at
        )
    )
    if oldest and oldest.tzinfo is None:
        oldest = oldest.replace(tzinfo=UTC)
    operations = {
        "oldest_due_at": oldest,
        "oldest_due_age_seconds": max(0, int((at - oldest).total_seconds())) if oldest else None,
        "last_relationship_observation": db.scalar(
            select(func.max(Relationship.last_checked_at)).where(
                Relationship.account_id == account_id
            )
        ),
        "last_collection_at": db.scalar(
            select(func.max(Source.last_collected_at)).where(Source.account_id == account_id)
        ),
        "unknown_writes": db.scalar(
            select(func.count())
            .select_from(Job)
            .where(
                Job.account_id == account_id,
                Job.dispatch_started_at.is_not(None),
                Job.state.in_(["unknown", "blocked"]),
                Job.error == "OUTCOME_UNKNOWN",
            )
        ),
    }
    return {
        "account": row(account),
        "job_counts": counts,
        "mode": account.mode,
        "live_metrics": None,
        "candidates_available": available_count,
        "queued_follows": queued_follows,
        "unfollows_due": due_count,
        "protected_accounts": protected_count,
        "follows_used": usage.get("follows", 0),
        "unfollows_used": usage.get("unfollows", 0),
        "requests_used": usage.get("requests", 0),
        "operations": operations,
        "run": {
            "processed": counts.get("simulated", 0) + counts.get("succeeded", 0),
            "skipped": counts.get("skipped", 0),
            "failed": counts.get("failed", 0),
            "review": counts.get("unknown", 0) + counts.get("blocked", 0),
        },
        "chart": [],
        "next_job": row(next_job) if next_job else None,
    }


@app.get("/api/v1/sources", response_model=Page[SourceResponse])
def sources(
    account_id: str,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    q: str = "",
    kind: str | None = None,
    db: Session = Depends(get_db),
):
    required(db, Account, account_id)
    query = (
        select(Source)
        .where(Source.account_id == account_id)
        .order_by(Source.created_at.desc(), Source.id)
    )
    if q:
        query = query.where(
            Source.label.icontains(q, autoescape=True) | Source.value.icontains(q, autoescape=True)
        )
    if kind:
        query = query.where(Source.kind == kind)
    total = db.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
    return {
        "items": [source_view(db, x) for x in db.scalars(query.offset(offset).limit(limit))],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@app.post("/api/v1/sources", status_code=201, response_model=SourceResponse)
def source_create(body: SourceCreate, db: Session = Depends(get_db)):
    account = required(db, Account, body.account_id)
    if body.kind == "profile_mutuals" and account.mode != "live":
        raise HTTPException(409, "Mutual sources require a real Instagram account")
    source = Source(**body.model_dump())
    db.add(source)
    db.add(
        AuditEvent(
            account_id=body.account_id,
            event="source_created",
            detail={"kind": body.kind, "value": body.value},
        )
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Source already exists") from None
    return source_view(db, source)


@app.get("/api/v1/relationships", response_model=Page[RelationshipResponse])
def relationships(
    account_id: str,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    q: str = "",
    state: str | None = None,
    target_ids: list[str] = Query(default=[]),
    db: Session = Depends(get_db),
):
    required(db, Account, account_id)
    query = (
        select(Relationship)
        .join(Target, Target.id == Relationship.target_id)
        .where(Relationship.account_id == account_id)
    )
    if len(target_ids) > 100:
        raise HTTPException(422, "At most 100 target IDs")
    if target_ids:
        query = query.where(Relationship.target_id.in_(target_ids))
    if q:
        query = query.where(
            Target.username.icontains(q, autoescape=True) | Target.id.icontains(q, autoescape=True)
        )
    if state == "protected":
        query = query.where(Relationship.protected.is_(True))
    elif state == "previously_followed":
        query = query.where(
            Relationship.ever_followed.is_(True), Relationship.observed_state == "none"
        )
    elif state in {"following", "requested", "none", "unknown"}:
        query = query.where(Relationship.observed_state == state)
    total = db.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
    config, at = account_settings(db, account_id), now()
    return {
        "items": [
            relationship_view(db, x, config=config, at=at)
            for x in db.scalars(query.order_by(Relationship.id).offset(offset).limit(limit))
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@app.patch("/api/v1/relationships/{relationship_id}", response_model=RelationshipResponse)
def relationship_patch(
    relationship_id: str, body: RelationshipPatch, db: Session = Depends(get_db)
):
    rel = db.scalar(
        select(Relationship).where(Relationship.id == relationship_id).with_for_update()
    )
    if rel is None:
        raise HTTPException(404, "Not found")
    if rel.version != body.expected_version:
        raise HTTPException(409, "Stale version; refresh and retry")
    changes = body.model_dump(exclude={"expected_version"}, exclude_none=True)
    for key, value in changes.items():
        setattr(rel, key, value)
    rel.version += 1
    db.add(
        AuditEvent(
            account_id=rel.account_id,
            event="relationship_changed",
            detail={"target_id": rel.target_id, **changes},
        )
    )
    db.commit()
    return relationship_view(db, rel)


@app.get("/api/v1/jobs", response_model=Page[JobResponse])
def jobs(
    account_id: str,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    state: str | None = None,
    kind: str | None = None,
    date: str | None = None,
    needs_review: bool = False,
    dm_sender_id: str | None = None,
    db: Session = Depends(get_db),
):
    required(db, Account, account_id)
    query = select(Job).where(Job.account_id == account_id)
    if dm_sender_id:
        inbox = sender_inbox(db, account_id, dm_sender_id)
        if not inbox:
            raise HTTPException(404, "Approved sender not found")
        query = poll_jobs(db, inbox)
    if state:
        query = query.where(Job.state.in_(state.split(",")))
    if needs_review:
        later_connection = aliased(Job)
        connected_since = (
            select(later_connection.id)
            .where(
                later_connection.account_id == Job.account_id,
                later_connection.kind == "connect",
                later_connection.state == "succeeded",
                later_connection.created_at > Job.created_at,
            )
            .exists()
        )
        query = query.where(
            Job.state.in_(("failed", "unknown", "blocked")),
            (Job.kind != "connect") | ~connected_since,
        )
    if kind:
        query = query.where(Job.kind == kind)
    if date in {"today", "week"}:
        zone = ZoneInfo(account_settings(db, account_id)["timezone"])
        if date == "today":
            local_day = now().astimezone(zone).date()
            start = datetime.combine(local_day, time.min, zone).astimezone(UTC)
            end = datetime.combine(local_day + timedelta(days=1), time.min, zone).astimezone(UTC)
        else:
            start, end = now() - timedelta(days=7), now() + timedelta(days=7)
        query = query.where(Job.scheduled_at >= start, Job.scheduled_at < end)
    total = db.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
    return {
        "items": [
            row(x)
            for x in db.scalars(
                query.order_by(Job.created_at.desc(), Job.id).offset(offset).limit(limit)
            )
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@app.post("/api/v1/jobs", status_code=202, response_model=JobResponse)
def queue(body: JobCreate, db: Session = Depends(get_db)):
    try:
        job, _ = enqueue(db, body)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Concurrent duplicate; fetch jobs before retry") from None
    return row(job)


@app.get("/api/v1/jobs/{job_id}", response_model=JobResponse)
def job_detail(job_id: str, db: Session = Depends(get_db)):
    return row(required(db, Job, job_id))


@app.post("/api/v1/jobs/{job_id}/cancel", response_model=JobResponse)
def cancel(job_id: str, db: Session = Depends(get_db)):
    job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
    if job is None:
        raise HTTPException(404, "Not found")
    if job.state != "queued":
        raise HTTPException(409, "Only queued jobs can be cancelled")
    job.state = "cancelled"
    job.completed_at = now()
    from .content_publication import finish_draft

    finish_draft(db, job, "cancelled")
    if job.kind == "follow":
        marker = db.scalar(select(FollowIntent).where(FollowIntent.job_id == job.id))
        if marker:
            db.delete(marker)
    if job.kind == "discover" and job.payload.get("source_id"):
        required(db, Source, job.payload["source_id"]).collection_state = "idle"
    db.add(AuditEvent(account_id=job.account_id, event="job_cancelled", detail={"job_id": job.id}))
    db.commit()
    return row(job)


@app.get("/api/v1/actions", response_model=Page[ActionResponse])
def actions(
    account_id: str,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    required(db, Account, account_id)
    query = select(Action).join(Job, Job.id == Action.job_id).where(Job.account_id == account_id)
    total = db.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
    return {
        "items": [
            row(x)
            for x in db.scalars(
                query.order_by(Action.created_at.desc(), Action.id).offset(offset).limit(limit)
            )
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@app.patch("/api/v1/sources/{source_id}", response_model=SourceResponse)
def source_patch(source_id: str, body: SourcePatch, db: Session = Depends(get_db)):
    source = db.scalar(select(Source).where(Source.id == source_id).with_for_update())
    if source is None:
        raise HTTPException(404, "Not found")
    source.enabled = body.enabled
    db.add(
        AuditEvent(
            account_id=source.account_id,
            event="source_changed",
            detail={"source_id": source_id, "enabled": body.enabled},
        )
    )
    db.commit()
    return source_view(db, source)


@app.get("/api/v1/sources/{source_id}", response_model=SourceResponse)
def source_detail(source_id: str, db: Session = Depends(get_db)):
    return source_view(db, required(db, Source, source_id))


@app.post("/api/v1/sources/{source_id}/collect", status_code=202, response_model=JobResponse)
def collect(source_id: str, body: Intent, db: Session = Depends(get_db)):
    source = required(db, Source, source_id)
    db.scalar(select(Account).where(Account.id == source.account_id).with_for_update())
    source = db.scalar(select(Source).where(Source.id == source_id).with_for_update())
    if source is None:
        raise HTTPException(404, "Not found")
    digest = fingerprint({"source_id": source_id, "operation": "collect"})
    saved = receipt(db, source.account_id, body.idempotency_key, "collect", digest)
    if saved:
        return row(required(db, Job, saved.job_id))
    old = db.scalar(
        select(Job).where(
            Job.account_id == source.account_id, Job.idempotency_key == body.idempotency_key
        )
    )
    if old:
        if old.kind != "discover" or old.payload.get("source_id") != source_id:
            raise HTTPException(409, "Idempotency key belongs to a different operation")
        remember(db, source.account_id, body.idempotency_key, "collect", digest, job_id=old.id)
        db.commit()
        return row(old)
    if not source.enabled:
        raise HTTPException(409, "Source is disabled")
    # Inspect all active discoveries rather than mistaking another source for this one.
    for active in db.scalars(
        select(Job).where(
            Job.account_id == source.account_id,
            Job.kind == "discover",
            Job.state.in_(["queued", "running"]),
        )
    ):
        if active.payload.get("source_id") == source_id:
            remember(
                db, source.account_id, body.idempotency_key, "collect", digest, job_id=active.id
            )
            db.commit()
            return row(active)
    if source.collection_complete:
        account = required(db, Account, source.account_id)
        if account.mode == "mock":
            raise HTTPException(
                409, "Sample source exhausted; add another source to collect a new sample"
            )
        source.collection_cursor, source.collection_complete = None, False
    account = required(db, Account, source.account_id)
    if account.mode == "live" and (
        settings().adapter_mode == "mock" or account.session_state != "connected"
    ):
        raise HTTPException(
            409, "Connect the account and configure read-only execution before collecting"
        )
    configured = account_settings(db, source.account_id)
    job = Job(
        account_id=source.account_id,
        kind="discover",
        idempotency_key=body.idempotency_key,
        payload={
            "source_id": source_id,
            "cursor": int(source.collection_cursor or 0)
            if account.mode == "mock"
            else source.collection_cursor,
            "remaining": min(source.requested_limit, configured["collection_batch_size"]),
        },
    )
    source.collection_state = "queued"
    db.add(job)
    db.flush()
    remember(db, source.account_id, body.idempotency_key, "collect", digest, job_id=job.id)
    db.add(
        AuditEvent(
            account_id=source.account_id,
            event="collection_queued",
            detail={"source_id": source_id, "job_id": job.id},
        )
    )
    db.commit()
    return row(job)


@app.get("/api/v1/candidates", response_model=Page[CandidateResponse])
def candidates(
    account_id: str,
    source_id: str | None = None,
    q: str = "",
    category: str | None = None,
    relationship: str | None = None,
    available: str | None = None,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    required(db, Account, account_id)
    if source_id and required(db, Source, source_id).account_id != account_id:
        raise HTTPException(404, "Not found")
    total = db.scalar(
        select(func.count()).select_from(
            candidate_query(account_id, source_id, q, category, relationship, available).subquery()
        )
    )
    return {
        "items": candidate_views(
            db, account_id, source_id, q, category, relationship, available, limit, offset
        ),
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@app.post("/api/v1/jobs/bulk", status_code=202, response_model=BulkResult)
def bulk(body: BulkJobs, db: Session = Depends(get_db)):
    required(db, Account, body.account_id)
    db.scalar(select(Account).where(Account.id == body.account_id).with_for_update())
    digest = fingerprint(
        {
            "account_id": body.account_id,
            "target_ids": sorted(set(body.target_ids)),
            "operation": "bulk_follow",
        }
    )
    saved = receipt(db, body.account_id, body.idempotency_key, "bulk", digest)
    if saved:
        return saved.result
    if len(set(body.target_ids)) > account_settings(db, body.account_id)["jobs_per_run"]:
        raise HTTPException(422, "Selection exceeds the configured jobs per reviewed batch")
    accepted, existing, rejected = [], [], []
    for target_id in dict.fromkeys(body.target_ids):
        try:
            job, duplicate = enqueue(
                db,
                JobCreate(
                    account_id=body.account_id,
                    target_id=target_id,
                    kind="follow",
                    idempotency_key="bulk:"
                    + fingerprint({"key": body.idempotency_key, "target_id": target_id}),
                ),
            )
            (existing if duplicate else accepted).append(row(job))
        except HTTPException as exc:
            rejected.append({"target_id": target_id, "reason": str(exc.detail)})
    result = {"jobs": accepted, "existing": existing, "rejected": rejected}
    remember(
        db, body.account_id, body.idempotency_key, "bulk", digest, result=jsonable_encoder(result)
    )
    db.commit()
    return result


@app.post(
    "/api/v1/relationships/{relationship_id}/recheck", status_code=202, response_model=JobResponse
)
def recheck(relationship_id: str, body: Intent, db: Session = Depends(get_db)):
    rel = required(db, Relationship, relationship_id)
    return queue(
        JobCreate(
            account_id=rel.account_id,
            target_id=rel.target_id,
            kind="reconcile",
            idempotency_key=body.idempotency_key,
        ),
        db,
    )


@app.get("/api/v1/accounts/{account_id}/settings", response_model=SettingsResponse)
def get_settings(account_id: str, db: Session = Depends(get_db)):
    required(db, Account, account_id)
    return account_settings(db, account_id)


@app.patch("/api/v1/accounts/{account_id}/settings", response_model=SettingsResponse)
def patch_settings(account_id: str, body: SettingsPatch, db: Session = Depends(get_db)):
    # Account lock also serializes first creation of settings.
    account = db.scalar(select(Account).where(Account.id == account_id).with_for_update())
    if account is None:
        raise HTTPException(404, "Not found")
    stored = db.get(AccountSettings, account_id)
    current = stored.version if stored else 1
    if body.expected_version != current:
        raise HTTPException(409, "Stale version; refresh settings and retry")
    if stored is None:
        stored = AccountSettings(account_id=account_id, version=current, values={})
        db.add(stored)
    values = body.model_dump(exclude={"expected_version"})
    if "music_library" not in body.model_fields_set:
        values["music_library"] = (stored.values or {}).get("music_library")
    if "ranked_follow_mode" not in body.model_fields_set:
        values["ranked_follow_mode"] = (stored.values or {}).get("ranked_follow_mode", "review")
    for key in ("shuffle_dm_music", "dm_self_receipts", "caption_sources"):
        if key not in body.model_fields_set:
            values[key] = (stored.values or {}).get(key)
    from .ranked_follows import replan_queued_follows

    replan_queued_follows(db, account_id, stored.values or {}, values, now())
    stored.values = values
    stored.version = current + 1
    db.add(
        AuditEvent(
            account_id=account_id, event="settings_changed", detail={"version": stored.version}
        )
    )
    db.commit()
    return account_settings(db, account_id)


@app.get("/api/v1/audit-events", response_model=Page[ActivityResponse])
def audit(
    account_id: str,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    q: str = "",
    category: str | None = None,
    db: Session = Depends(get_db),
):
    account = required(db, Account, account_id)
    total = db.scalar(
        select(func.count()).select_from(
            activity_query(account_id, account.username, account.mode, q, category).subquery()
        )
    )
    return {
        "items": activity_views(db, account_id, limit, offset, q, category),
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@app.get("/api/v1/worker-status", response_model=WorkerResponse)
def worker_status(account_id: str, db: Session = Depends(get_db)):
    required(db, Account, account_id)
    heartbeat = db.scalar(
        select(WorkerHeartbeat).order_by(WorkerHeartbeat.observed_at.desc()).limit(1)
    )
    observed = heartbeat.observed_at if heartbeat else None
    active = db.scalar(
        select(func.max(Job.heartbeat_at)).where(
            Job.account_id == account_id, Job.state == "running"
        )
    )
    if active and (observed is None or active > observed):
        observed = active
    if observed and observed.tzinfo is None:
        observed = observed.replace(tzinfo=UTC)
    return {
        "status": "unavailable"
        if observed is None
        else "online"
        if (now() - observed).total_seconds() <= 60
        else "stale",
        "last_heartbeat": observed,
        "simulated": settings().adapter_mode == "mock",
    }


@app.get("/api/v1/accounts/{account_id}/connection", response_model=ConnectionResponse)
def connection_status(account_id: str, db: Session = Depends(get_db)):
    account = required(db, Account, account_id)
    metadata = db.get(AccountSession, account_id)
    mode = settings().adapter_mode
    connected = account.session_state == "connected" and not account.restricted
    return {
        "session_state": account.session_state,
        "transport_mode": mode,
        "last_validated_at": metadata.last_validated_at if metadata else None,
        "library_version": metadata.library_version if metadata else None,
        "capabilities": metadata.capabilities if metadata else {},
        "import_complete": metadata.import_complete if metadata else False,
        "can_collect": account.mode == "mock" or (connected and mode in {"read_only", "live"}),
        "can_follow": connected and mode == "live" and account.live_acquisition_enabled,
        "can_unfollow": connected and mode == "live" and account.live_cleanup_enabled,
    }


@app.post(
    "/api/v1/accounts/{account_id}/import-following", status_code=202, response_model=JobResponse
)
def import_following(account_id: str, body: Intent, db: Session = Depends(get_db)):
    account = db.scalar(select(Account).where(Account.id == account_id).with_for_update())
    if account is None:
        raise HTTPException(404, "Not found")
    digest = fingerprint({"account_id": account_id, "operation": "import_following"})
    saved = receipt(db, account_id, body.idempotency_key, "import_following", digest)
    if saved:
        return row(required(db, Job, saved.job_id))
    if (
        account.mode != "live"
        or account.session_state != "connected"
        or settings().adapter_mode == "mock"
    ):
        raise HTTPException(409, "A connected read-only account is required for following import")
    metadata = required(db, AccountSession, account_id)
    for active in db.scalars(
        select(Job).where(
            Job.account_id == account_id,
            Job.kind == "import_following",
            Job.state.in_(["queued", "running"]),
        )
    ):
        remember(db, account_id, body.idempotency_key, "import_following", digest, job_id=active.id)
        db.commit()
        return row(active)
    if metadata.import_complete:
        metadata.import_cursor, metadata.import_complete = None, False
    job = Job(
        account_id=account_id,
        kind="import_following",
        idempotency_key=body.idempotency_key,
        payload={
            "cursor": metadata.import_cursor,
            "remaining": account_settings(db, account_id)["collection_batch_size"],
        },
    )
    db.add(job)
    db.flush()
    remember(db, account_id, body.idempotency_key, "import_following", digest, job_id=job.id)
    db.add(
        AuditEvent(
            account_id=account_id, event="following_import_queued", detail={"job_id": job.id}
        )
    )
    db.commit()
    return row(job)


@app.get("/api/v1/jobs/{job_id}/attempts", response_model=Page[AttemptResponse])
def attempts(
    job_id: str,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    required(db, Job, job_id)
    query = (
        select(ActionAttempt)
        .where(ActionAttempt.job_id == job_id)
        .order_by(ActionAttempt.started_at.desc(), ActionAttempt.id)
    )
    total = db.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
    items = []
    for attempt in db.scalars(query.offset(offset).limit(limit)):
        events = db.scalars(
            select(AttemptEvent)
            .where(AttemptEvent.attempt_id == attempt.id)
            .order_by(AttemptEvent.created_at, AttemptEvent.id)
        ).all()
        items.append({**row(attempt), "events": [row(event) for event in events]})
    return {"items": items, "total": total, "limit": limit, "offset": offset}


def recovery_job(db, original, body, operation):
    db.scalar(select(Account).where(Account.id == original.account_id).with_for_update())
    original = db.scalar(select(Job).where(Job.id == original.id).with_for_update())
    digest = fingerprint({"job_id": original.id, "operation": operation})
    saved = receipt(db, original.account_id, body.idempotency_key, operation, digest)
    if saved:
        return required(db, Job, saved.job_id)
    if operation == "reconcile":
        if (
            original.kind not in {"follow", "unfollow"}
            or not original.dispatch_started_at
            or not (
                original.state == "unknown"
                or (original.state == "blocked" and original.error == "OUTCOME_UNKNOWN")
            )
        ):
            raise HTTPException(409, "Only an uncertain dispatched mutation can be reconciled")
        kind, payload = "reconcile", {"reconcile_job_id": original.id}
    else:
        if (
            original.kind not in {"reconcile", "discover"}
            or original.state != "failed"
            or original.dispatch_started_at
        ):
            raise HTTPException(409, "Only definitely failed reads can be retried")
        depth = int(original.payload.get("retry_depth", 0)) + 1
        if depth > 3:
            raise HTTPException(409, "Read retry limit reached; review the source or session")
        kind, payload = original.kind, {**original.payload, "retry_depth": depth}
    job = Job(
        account_id=original.account_id,
        target_id=original.target_id,
        kind=kind,
        idempotency_key="recovery:"
        + fingerprint(
            {"key": body.idempotency_key, "original": original.id, "operation": operation}
        ),
        payload=payload,
        scheduled_at=now() + timedelta(seconds=2 if operation == "retry" else 0),
    )
    db.add(job)
    db.flush()
    remember(db, original.account_id, body.idempotency_key, operation, digest, job_id=job.id)
    db.add(
        AuditEvent(
            account_id=original.account_id,
            event=f"{operation}_queued",
            detail={"job_id": job.id, "original_job_id": original.id},
        )
    )
    db.commit()
    return job


@app.post("/api/v1/jobs/{job_id}/reconcile", status_code=202, response_model=JobResponse)
def reconcile_job(job_id: str, body: Intent, db: Session = Depends(get_db)):
    return row(recovery_job(db, required(db, Job, job_id), body, "reconcile"))


@app.post("/api/v1/jobs/{job_id}/retry", status_code=202, response_model=JobResponse)
def retry_job(job_id: str, body: Intent, db: Session = Depends(get_db)):
    return row(recovery_job(db, required(db, Job, job_id), body, "retry"))


@app.patch("/api/v1/sources/{source_id}/schedule", response_model=SourceResponse)
def source_schedule(source_id: str, body: SourceSchedulePatch, db: Session = Depends(get_db)):
    existing = required(db, Source, source_id)
    db.scalar(select(Account).where(Account.id == existing.account_id).with_for_update())
    # Lock queued jobs before the source, matching simulation's job → source order.
    queued = db.scalars(
        select(Job)
        .where(
            Job.account_id == existing.account_id,
            Job.kind == "discover",
            Job.state == "queued",
            Job.payload["source_id"].as_string() == source_id,
        )
        .with_for_update()
    ).all()
    source = db.scalar(
        select(Source)
        .where(Source.id == source_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if source.schedule_version != body.expected_version:
        raise HTTPException(409, "Stale version; refresh the collection schedule")
    source.schedule_enabled, source.schedule_local_time = body.enabled, body.local_time
    source.schedule_version += 1
    config = account_settings(db, source.account_id)
    for job in queued:
        if not job.payload.get("recurring"):
            continue
        if not body.enabled:
            job.state, job.completed_at, job.error = "cancelled", now(), "schedule_disabled"
            source.collection_state = "idle"
        else:
            run_day = date.fromisoformat(
                job.payload.get(
                    "run_local_day",
                    now().astimezone(ZoneInfo(config["timezone"])).date().isoformat(),
                )
            )
            due = max(now(), scheduled_instant(run_day, body.local_time, config["timezone"]))
            job.scheduled_at = due if within_window(due, config) else next_window(due, config)
            job.payload = {
                **job.payload,
                "run_local_day": job.scheduled_at.astimezone(ZoneInfo(config["timezone"]))
                .date()
                .isoformat(),
            }
    db.add(
        AuditEvent(
            account_id=source.account_id,
            event="source_schedule_changed",
            detail={"source_id": source.id, "enabled": body.enabled, "local_time": body.local_time},
        )
    )
    db.commit()
    return source_view(db, source)
