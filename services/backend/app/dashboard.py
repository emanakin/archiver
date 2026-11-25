"""Application queries and durable intent creation, shared by API and worker."""

import hashlib
import json
from datetime import UTC, timedelta

from fastapi import HTTPException
from sqlalchemy import JSON, String, case, cast, column, func, literal, or_, select, true, union_all

from .cleanup_policy import cleanup_assessment
from .models import (
    Account,
    AccountSettings,
    Action,
    AuditEvent,
    Discovery,
    FollowIntent,
    InsightCheck,
    InsightWatch,
    IntentReceipt,
    Job,
    LikerObservation,
    Relationship,
    Source,
    Target,
    now,
)
from .policy import eligibility
from .schemas import SettingsValues
from .timing import local_day


def row(obj):
    values = {c.name: getattr(obj, c.name) for c in obj.__table__.columns}
    # SQLite is used by unit tests; PostgreSQL retains UTC offsets itself.
    return {
        key: value.replace(tzinfo=UTC)
        if hasattr(value, "tzinfo") and value.tzinfo is None
        else value
        for key, value in values.items()
    }


def required(db, model, object_id):
    obj = db.get(model, object_id)
    if obj is None:
        raise HTTPException(404, "Not found")
    return obj


def account_settings(db, account_id):
    stored = db.get(AccountSettings, account_id)
    values = SettingsValues(**(stored.values if stored else {})).model_dump()
    return {**values, "version": stored.version if stored else 1}


def source_view(db, source):
    count = db.scalar(
        select(func.count()).select_from(Discovery).where(Discovery.source_id == source.id)
    )
    overlapping = (
        select(Discovery.target_id)
        .join(Source, Source.id == Discovery.source_id)
        .where(Source.account_id == source.account_id)
        .group_by(Discovery.target_id)
        .having(func.count() > 1)
    )
    overlap = db.scalar(
        select(func.count())
        .select_from(Discovery)
        .where(Discovery.source_id == source.id, Discovery.target_id.in_(overlapping))
    )
    return {**row(source), "discovered_count": count, "overlap_count": overlap}


def relationship_view(db, rel, config=None, at=None):
    target = db.get(Target, rel.target_id)
    sources = db.scalars(
        select(Discovery.source_id)
        .join(Source, Source.id == Discovery.source_id)
        .where(Source.account_id == rel.account_id, Discovery.target_id == rel.target_id)
    ).all()
    uncertain = db.scalar(
        select(Job.id)
        .where(
            Job.account_id == rel.account_id,
            Job.target_id == rel.target_id,
            Job.kind.in_(["follow", "unfollow"]),
            Job.state.in_(["unknown", "blocked"]),
            Job.error == "OUTCOME_UNKNOWN",
            Job.dispatch_started_at.is_not(None),
        )
        .limit(1)
    )
    return {
        **row(rel),
        "username": target.username if target else rel.target_id,
        "source_ids": list(sources),
        "cleanup": cleanup_assessment(
            rel, config or account_settings(db, rel.account_id), at or now(), bool(uncertain)
        ),
    }


def candidate_query(
    account_id, source_id=None, q="", category=None, relationship=None, available=None
):
    reserved = (
        select(FollowIntent.id)
        .join(Job, Job.id == FollowIntent.job_id)
        .where(
            FollowIntent.account_id == Relationship.account_id,
            FollowIntent.target_id == Relationship.target_id,
            or_(
                Job.dispatch_started_at.is_not(None),
                Job.state.not_in(["failed", "skipped", "cancelled"]),
            ),
        )
        .exists()
    )
    reason = case(
        (Relationship.excluded.is_(True), "excluded"),
        (Relationship.protected.is_(True), "protected"),
        (Relationship.ever_followed.is_(True), "previously_followed"),
        (Relationship.observed_state.in_(["following", "requested"]), "already_connected"),
        (Relationship.observed_state == "unknown", "relationship_unknown"),
        (reserved, "follow_already_queued"),
        else_=None,
    ).label("reason")
    label = case(
        (reason == "excluded", "excluded"),
        (reason == "protected", "protected"),
        (reason == "previously_followed", "previously_followed"),
        (
            (reason == "already_connected") & (Relationship.observed_state == "requested"),
            "requested",
        ),
        (reason == "already_connected", "already_following"),
        (reason.is_(None), "eligible"),
        else_="unknown",
    ).label("category")
    query = (
        select(Relationship, Target, reason, label)
        .join(Target, Target.id == Relationship.target_id)
        .where(Relationship.account_id == account_id)
    )
    discovered = (
        select(Discovery.target_id)
        .join(Source, Source.id == Discovery.source_id)
        .where(Source.account_id == account_id)
    )
    if source_id:
        discovered = discovered.where(Source.id == source_id)
    query = query.where(Relationship.target_id.in_(discovered))
    if q:
        query = query.where(
            Target.username.icontains(q, autoescape=True) | Target.id.icontains(q, autoescape=True)
        )
    if category:
        query = query.where(label == category)
    if relationship:
        query = query.where(Relationship.observed_state == relationship)
    if available == "active":
        query = query.where(Relationship.excluded.is_(False))
    elif available == "eligible":
        query = query.where(reason.is_(None))
    elif available == "ineligible":
        query = query.where(reason.is_not(None))
    return query.order_by(Target.id)


def audience_evidence(db, account_id):
    """Distinct positive observations within a rolling month, scoped to one workspace."""
    cutoff = now() - timedelta(days=30)
    profile_source = Source.kind.in_(["profile_followers", "profile_mutuals"])
    discoveries = (
        select(
            Discovery.target_id.label("candidate"),
            case((profile_source, "profile"), else_="post").label("signal"),
            case(
                (
                    profile_source,
                    func.coalesce(Source.resolved_instagram_id, literal("@") + Source.value),
                ),
                else_=func.replace(Source.value, "/reel/", "/p/"),
            ).label("identity"),
        )
        .join(Source, Source.id == Discovery.source_id)
        .where(Source.account_id == account_id, Discovery.last_seen_at >= cutoff)
    )
    watches = (
        select(
            LikerObservation.target_id,
            literal("post"),
            func.replace(InsightWatch.target, "/reel/", "/p/"),
        )
        .join(InsightWatch, InsightWatch.id == LikerObservation.watch_id)
        .where(InsightWatch.account_id == account_id, LikerObservation.last_seen_at >= cutoff)
    )
    # Both supported databases expose JSON array elements as rows with a value column.
    elements = (
        func.json_array_elements if db.get_bind().dialect.name == "postgresql" else func.json_each
    )
    item = elements(InsightCheck.items).table_valued(column("value", JSON)).alias("seen_user")
    followed_id = item.c.value["id"].as_string()
    unresolved_handles = select(Source.value).where(
        Source.account_id == account_id, profile_source, Source.resolved_instagram_id.is_(None)
    )
    references = (
        select(Relationship.target_id)
        .where(Relationship.account_id == account_id, Relationship.observed_state == "following")
        .union(
            select(Source.resolved_instagram_id).where(
                Source.account_id == account_id, profile_source
            )
        )
    )
    following = (
        select(InsightCheck.profile["id"].as_string(), literal("profile"), followed_id)
        .select_from(InsightCheck)
        .join(item, true())
        .where(
            InsightCheck.account_id == account_id,
            InsightCheck.kind == "following",
            InsightCheck.observed_at >= cutoff,
            or_(
                followed_id.in_(references),
                func.lower(item.c.value["username"].as_string()).in_(unresolved_handles),
            ),
        )
    )
    observed_likes = (
        select(followed_id, literal("post"), func.replace(InsightCheck.target, "/reel/", "/p/"))
        .select_from(InsightCheck)
        .join(item, true())
        .where(
            InsightCheck.account_id == account_id,
            InsightCheck.kind == "likers",
            InsightCheck.observed_at >= cutoff,
        )
    )
    evidence = discoveries.union(watches, following, observed_likes).subquery()
    return (
        select(
            evidence.c.candidate,
            func.count().filter(evidence.c.signal == "post").label("liked_posts"),
            func.count().filter(evidence.c.signal == "profile").label("shared_profiles"),
        )
        .group_by(evidence.c.candidate)
        .subquery()
    )


def candidate_views(
    db,
    account_id,
    source_id=None,
    q="",
    category=None,
    relationship=None,
    available=None,
    limit=None,
    offset=0,
    ranked_mode=None,
    ranked_excluded=None,
):
    query = candidate_query(account_id, source_id, q, category, relationship, available)
    evidence = audience_evidence(db, account_id)
    likes = func.coalesce(evidence.c.liked_posts, 0)
    profiles = func.coalesce(evidence.c.shared_profiles, 0)
    last_discovered = (
        select(func.max(Discovery.first_seen_at))
        .join(Source, Source.id == Discovery.source_id)
        .where(Source.account_id == account_id, Discovery.target_id == Target.id)
        .correlate(Target)
        .scalar_subquery()
    )
    query = (
        query.outerjoin(evidence, evidence.c.candidate == Target.id)
        .add_columns(likes, profiles)
        .order_by(None)
        .order_by(
            (query.selected_columns.reason.is_(None)).desc(),
            (likes > 0).desc(),
            (likes + profiles).desc(),
            likes.desc(),
            last_discovered.desc(),
            Target.id,
        )
    )
    if ranked_mode:
        query = query.where(
            Relationship.follows_back.is_(False),
            Target.id
            != select(Account.instagram_id).where(Account.id == account_id).scalar_subquery(),
            likes > 0
            if ranked_mode == "likers"
            else profiles >= 2
            if ranked_mode == "overlap"
            else true(),
        )
    if ranked_excluded is not None:
        query = query.where(Target.id.not_in(ranked_excluded))
    if limit is not None:
        query = query.offset(offset).limit(limit)
    rows = db.execute(query).all()
    if not rows:
        return []
    links_by_target = {}
    for link in db.scalars(
        select(Discovery)
        .join(Source, Source.id == Discovery.source_id)
        .where(
            Source.account_id == account_id, Discovery.target_id.in_([item[1].id for item in rows])
        )
        .order_by(Discovery.first_seen_at, Discovery.source_id)
    ):
        links_by_target.setdefault(link.target_id, []).append(link)
    results = []
    for _rel, target, reason, label, liked_posts, shared_profiles in rows:
        links = links_by_target[target.id]
        results.append(
            {
                "target_id": target.id,
                "username": target.username,
                "liked_posts": liked_posts,
                "shared_profiles": shared_profiles,
                "source_ids": [x.source_id for x in links],
                "first_seen_at": min(x.first_seen_at for x in links),
                "eligible": reason is None,
                "reason": reason,
                "category": label,
            }
        )
    return results


def fingerprint(values):
    return hashlib.sha256(
        json.dumps(values, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def receipt(db, account_id, key, operation, digest):
    saved = db.scalar(
        select(IntentReceipt).where(
            IntentReceipt.account_id == account_id, IntentReceipt.idempotency_key == key
        )
    )
    if saved and (saved.operation != operation or saved.fingerprint != digest):
        raise HTTPException(409, "Idempotency key belongs to a different operation")
    return saved


def remember(db, account_id, key, operation, digest, job_id=None, result=None):
    db.add(
        IntentReceipt(
            account_id=account_id,
            idempotency_key=key,
            operation=operation,
            fingerprint=digest,
            job_id=job_id,
            result=result,
        )
    )
    db.flush()


def release_unsent_follow(db, job):
    if (
        job.kind == "follow"
        and job.dispatch_started_at is None
        and job.state in {"failed", "skipped", "cancelled"}
    ):
        marker = db.scalar(
            select(FollowIntent).where(FollowIntent.job_id == job.id).with_for_update()
        )
        if marker:
            db.delete(marker)
            db.flush()


def enqueue(db, body):
    # Lock account first: all API acquisition intents use this ordering.
    account = db.scalar(select(Account).where(Account.id == body.account_id).with_for_update())
    if account is None:
        raise HTTPException(404, "Not found")
    values = body.model_dump(mode="json", exclude={"idempotency_key"})
    if body.scheduled_at:
        values["scheduled_at"] = body.scheduled_at.astimezone(UTC).isoformat()
    digest = fingerprint(values)
    saved = receipt(db, body.account_id, body.idempotency_key, "job", digest)
    if saved:
        return required(db, Job, saved.job_id), True
    old = db.scalar(
        select(Job).where(
            Job.account_id == body.account_id, Job.idempotency_key == body.idempotency_key
        )
    )
    if old:
        if old.request_fingerprint != digest:
            raise HTTPException(409, "Idempotency key belongs to a different operation")
        remember(db, body.account_id, body.idempotency_key, "job", digest, job_id=old.id)
        return old, True
    rel = db.scalar(
        select(Relationship)
        .where(Relationship.account_id == body.account_id, Relationship.target_id == body.target_id)
        .with_for_update()
    )
    reason = eligibility(body.kind, rel)
    if reason:
        raise HTTPException(409, reason)
    if body.kind == "follow":
        marker = db.scalar(
            select(FollowIntent).where(
                FollowIntent.account_id == body.account_id, FollowIntent.target_id == body.target_id
            )
        )
        if marker:
            existing = db.get(Job, marker.job_id)
            if existing.dispatch_started_at is None and existing.state in {
                "failed",
                "skipped",
                "cancelled",
            }:
                release_unsent_follow(db, existing)
            else:
                remember(
                    db, body.account_id, body.idempotency_key, "job", digest, job_id=existing.id
                )
                return existing, True
    job = Job(**body.model_dump(exclude_none=True), request_fingerprint=digest)
    db.add(job)
    db.flush()
    if body.kind == "follow":
        db.add(FollowIntent(account_id=body.account_id, target_id=body.target_id, job_id=job.id))
    db.add(
        AuditEvent(
            account_id=body.account_id,
            event="job_queued",
            detail={"job_id": job.id, "kind": body.kind, "target_id": body.target_id},
        )
    )
    db.flush()
    remember(db, body.account_id, body.idempotency_key, "job", digest, job_id=job.id)
    return job, False


def activity_query(account_id, username, mode, q="", category=None):
    actions = (
        select(
            Action.id.label("id"),
            func.coalesce(Target.username, literal("Source collection")).label("target"),
            Action.kind.label("operation"),
            Action.outcome.label("result"),
            Action.detail.label("reason"),
            case(
                (Action.outcome.in_(["failed", "unknown", "blocked"]), "error"), else_="action"
            ).label("category"),
            Action.created_at.label("created_at"),
            case((Action.simulated.is_(True), "mock"), else_="live").label("mode"),
            Job.id.label("job_id"),
            Job.id.label("request_id"),
        )
        .join(Job, Job.id == Action.job_id)
        .outerjoin(Target, Target.id == Job.target_id)
        .where(Job.account_id == account_id)
    )
    audits = select(
        AuditEvent.id.label("id"),
        literal(username).label("target"),
        AuditEvent.event.label("operation"),
        literal("recorded").label("result"),
        cast(AuditEvent.detail, String).label("reason"),
        literal("configuration").label("category"),
        AuditEvent.created_at.label("created_at"),
        literal(mode).label("mode"),
        literal(None).label("job_id"),
        AuditEvent.id.label("request_id"),
    ).where(AuditEvent.account_id == account_id)
    records = union_all(actions, audits).subquery()
    query = select(records)
    if q:
        query = query.where(
            records.c.target.icontains(q, autoescape=True)
            | records.c.operation.icontains(q, autoescape=True)
            | records.c.reason.icontains(q, autoescape=True)
        )
    if category:
        query = query.where(records.c.category == category)
    return query.order_by(records.c.created_at.desc(), records.c.id.desc())


def activity_views(db, account_id, limit=50, offset=0, q="", category=None):
    account = required(db, Account, account_id)
    query = activity_query(account_id, account.username, account.mode, q, category)
    results = []
    for record in db.execute(query.limit(limit).offset(offset)).mappings():
        entry = dict(record)
        if entry["category"] == "configuration":
            detail = json.loads(entry["reason"])
            event = entry["operation"]
            summaries = {
                "pause_changed": "Queue paused." if detail.get("paused") else "Queue resumed.",
                "source_created": "Discovery source added.",
                "source_changed": "Discovery source enabled."
                if detail.get("enabled")
                else "Discovery source disabled.",
                "collection_queued": "Bounded source collection queued.",
                "job_queued": "Action queued. The relationship has not changed.",
                "job_cancelled": "Queued action cancelled before execution.",
                "settings_changed": "Workspace settings saved.",
                "relationship_changed": "Relationship protection and exclusion preferences updated.",
            }
            entry["reason"] = summaries.get(event, event.replace("_", " ").capitalize())
            entry["job_id"] = detail.get("job_id")
        results.append(entry)
    return results


def mock_collect(db, job):
    source = required(db, Source, job.payload["source_id"])
    if not source.enabled:
        source.collection_state = "idle"
        return "skipped", "Source disabled before collection"
    if job.payload.get("recurring") and not source.schedule_enabled:
        source.collection_state = "idle"
        return "skipped", "Recurring collection was disabled"
    if job.payload.get("recurring") and job.payload.get("run_local_day") != local_day(
        now(), account_settings(db, source.account_id)
    ):
        source.collection_state = "idle"
        return "skipped", "Scheduled collection belongs to an earlier local day"
    if required(db, Account, source.account_id).mode != "mock":
        return "blocked", "Mock discovery cannot run for a real account"
    cursor = int(job.payload.get("cursor", 0))
    remaining = int(job.payload["remaining"])
    count = min(10, remaining, max(0, 40 - cursor))
    suffix = hashlib.sha256(source.id.encode()).hexdigest()[:12]
    for index in range(cursor, cursor + count):
        target_id = f"demo-shared-{index}" if index < 5 else f"demo-{suffix}-{index}"
        target = db.get(Target, target_id)
        if target is None:
            db.add(
                Target(
                    id=target_id, username=f"sample_{'shared' if index < 5 else suffix[:5]}_{index}"
                )
            )
            db.flush()
        if not db.scalar(
            select(Relationship.id).where(
                Relationship.account_id == source.account_id, Relationship.target_id == target_id
            )
        ):
            db.add(
                Relationship(
                    account_id=source.account_id, target_id=target_id, observed_state="none"
                )
            )
        link = db.scalar(
            select(Discovery).where(
                Discovery.source_id == source.id, Discovery.target_id == target_id
            )
        )
        if link:
            link.last_seen_at = now()
        else:
            db.add(Discovery(source_id=source.id, target_id=target_id))
    cursor += count
    remaining -= count
    source.collection_cursor = str(cursor)
    source.collection_complete = cursor >= 40
    source.last_collected_at = now()
    if remaining > 0 and cursor < 40:
        db.add(
            Job(
                account_id=source.account_id,
                kind="discover",
                idempotency_key=f"{job.id}:page:{cursor}",
                payload={
                    **job.payload,
                    "source_id": source.id,
                    "cursor": cursor,
                    "remaining": remaining,
                },
            )
        )
        source.collection_state = "running"
    else:
        source.collection_state = "completed" if source.collection_complete else "partial"
    return "simulated", f"Collected {count} fictional candidates; zero Instagram requests."
