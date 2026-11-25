"""Pace ranked follows across the account's operating window."""

import random
from datetime import UTC, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .dashboard import account_settings, candidate_views, enqueue, release_unsent_follow
from .models import Account, BudgetUsage, Job, now
from .schemas import JobCreate
from .timing import local_day, within_window


def replan_queued_follows(db, account_id, previous, updated, at):
    fields = ("follow_pacing", "follows_per_day", "follows_per_hour_min", "follows_per_hour_max")
    if all(previous.get(field) == updated.get(field) for field in fields):
        return
    jobs = db.scalars(
        select(Job)
        .where(
            Job.account_id == account_id,
            Job.kind == "follow",
            Job.state == "queued",
            Job.dispatch_started_at.is_(None),
            Job.payload["ranked_day"].as_string().is_not(None),
        )
        .with_for_update()
    ).all()
    for job in jobs:
        job.state, job.completed_at, job.error = "cancelled", at, "pacing_changed"
        release_unsent_follow(db, job)


def follow_interval(config):
    start, end = (
        int(value[:2]) * 60 + int(value[3:])
        for value in (config["window_start"], config["window_end"])
    )
    minutes = (end - start) % 1440 or 1440
    return timedelta(minutes=minutes / config["follows_per_day"])


def hourly_slots(account_id, at, config):
    """Restart-stable jitter inside evenly distributed slots; no missed-hour backlog."""
    hour = at.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    rng = random.Random(
        f"{account_id}:{hour.isoformat()}:{config['follows_per_hour_min']}:"
        f"{config['follows_per_hour_max']}"
    )
    count = rng.randint(config["follows_per_hour_min"], config["follows_per_hour_max"])
    width = 3600 / count
    return [
        (
            f"{hour.isoformat()}:{index}",
            hour + timedelta(seconds=(index + rng.uniform(0.15, 0.85)) * width),
            hour + timedelta(seconds=(index + 1) * width),
        )
        for index in range(count)
    ]


def queue_ranked_cohort(db, account_id, at):
    account = db.scalar(select(Account).where(Account.id == account_id).with_for_update())
    if (
        not account
        or account.mode != "live"
        or not account.live_acquisition_enabled
        or account.session_state != "connected"
        or not account.read_only_verified_at
        or account.paused
        or account.restricted
        or account.automatic_pause_reason
        or (account.cooldown_until and account.cooldown_until > at)
    ):
        return
    config = account_settings(db, account_id)
    mode = config["ranked_follow_mode"]
    if mode == "review" or config["follows_per_day"] == 0 or not within_window(at, config):
        return
    day = local_day(at, config)
    if db.scalar(
        select(Job.id)
        .where(
            Job.account_id == account_id,
            Job.kind == "follow",
            Job.state.in_(["queued", "running", "unknown", "blocked"]),
        )
        .limit(1)
    ):
        return
    usage = db.get(BudgetUsage, (account_id, day, "follows"))
    ceiling = config["follows_per_day"]
    if ceiling is not None and (usage.consumed + usage.reserved if usage else 0) >= ceiling:
        return
    slot = None
    if config["follow_pacing"] == "hourly":
        for key, due, expires in hourly_slots(account_id, at, config):
            if expires <= at:
                continue
            if db.scalar(
                select(Job.id)
                .where(
                    Job.account_id == account_id,
                    Job.kind == "follow",
                    Job.payload["ranked_slot"].as_string() == key,
                    Job.error.is_distinct_from("pacing_changed"),
                )
                .limit(1)
            ):
                continue
            slot = (key, due, expires)
            break
        if slot is None:
            return
    last_dispatch = db.scalar(
        select(func.max(Job.dispatch_started_at)).where(
            Job.account_id == account_id, Job.kind == "follow"
        )
    )
    if last_dispatch and slot is None:
        last_dispatch = (
            last_dispatch.replace(tzinfo=UTC) if not last_dispatch.tzinfo else last_dispatch
        )
        if at < last_dispatch + follow_interval(config):
            return
    tried = select(Job.target_id).where(
        Job.account_id == account_id,
        Job.kind == "follow",
        Job.target_id.is_not(None),
        Job.payload["ranked_day"].as_string() == day,
    )
    candidates = candidate_views(
        db,
        account_id,
        available="eligible",
        ranked_mode=mode,
        ranked_excluded=tried,
        limit=1,
    )
    for candidate in candidates:
        target = candidate["target_id"]
        job, existing = enqueue(
            db,
            JobCreate(
                account_id=account_id,
                target_id=target,
                kind="follow",
                idempotency_key=f"ranked:{day}:{target}",
                scheduled_at=max(at, slot[1]) if slot else at,
            ),
        )
        if not existing:
            job.payload = {
                "ranked_day": day,
                "ranked_mode": mode,
                "liked_posts": candidate["liked_posts"],
                "shared_profiles": candidate["shared_profiles"],
            }
            if slot:
                job.payload = {
                    **job.payload,
                    "ranked_slot": slot[0],
                    "ranked_expires_at": slot[2].isoformat(),
                }
                job.priority = 1


def materialize(engine, clock=now):
    with Session(engine) as db:
        ids = db.scalars(select(Account.id).where(Account.live_acquisition_enabled.is_(True))).all()
    for account_id in ids:
        with Session(engine) as db, db.begin():
            queue_ranked_cohort(db, account_id, clock())
