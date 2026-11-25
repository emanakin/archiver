from datetime import timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from .account_lane import AccountLane
from .dashboard import account_settings
from .models import Account, Job, Source, now
from .timing import local_day, next_window, scheduled_instant, within_window


def collection_retry_at(job, at, config):
    if job.kind != "discover" or not job.payload.get("recurring"):
        return None
    if not 1 <= job.attempt_count <= 3:
        return None
    due = at + timedelta(minutes=5 * (2 ** (job.attempt_count - 1)))
    if local_day(due, config) == job.payload.get("run_local_day") and within_window(due, config):
        return due
    return None


def materialize_collections(engine, clock=now, allow_live=False, allow_mock=True):
    with Session(engine) as db:
        account_ids = db.scalars(
            select(Source.account_id)
            .where(Source.enabled.is_(True), Source.schedule_enabled.is_(True))
            .distinct()
        ).all()
    created = 0
    for account_id in account_ids:
        with AccountLane(engine, account_id) as lane:
            if not lane.acquired:
                continue
            at = clock()
            with Session(engine) as db, db.begin():
                account = db.scalar(
                    select(Account).where(Account.id == account_id).with_for_update()
                )
                if account.mode == "mock" and not allow_mock:
                    continue
                if account.mode == "live" and (
                    not allow_live or account.session_state != "connected" or account.restricted
                ):
                    continue
                config = account_settings(db, account_id)
                day = at.astimezone(ZoneInfo(config["timezone"])).date()
                # Match the simulator/API lock order: account → jobs → sources.
                # Pausing across several days keeps checkpoints, never a backlog.
                for queued in db.scalars(
                    select(Job)
                    .where(
                        Job.account_id == account_id, Job.kind == "discover", Job.state == "queued"
                    )
                    .with_for_update()
                ):
                    if (
                        queued.payload.get("recurring")
                        and queued.payload.get("run_local_day", day.isoformat()) < day.isoformat()
                    ):
                        queued.state, queued.completed_at, queued.error = (
                            "cancelled",
                            at,
                            "schedule_expired",
                        )
                db.flush()
                for source in db.scalars(
                    select(Source)
                    .where(
                        Source.account_id == account_id,
                        Source.enabled.is_(True),
                        Source.schedule_enabled.is_(True),
                    )
                    .with_for_update()
                ):
                    due = scheduled_instant(day, source.schedule_local_time, config["timezone"])
                    if due > at:
                        continue
                    # One current-day run, not a backlog burst of missed days.
                    key = f"daily-collection:{source.id}:{day.isoformat()}"
                    if db.scalar(
                        select(Job.id).where(
                            Job.account_id == account_id, Job.idempotency_key == key
                        )
                    ):
                        continue
                    active = db.scalar(
                        select(Job.id)
                        .where(
                            Job.account_id == account_id,
                            Job.kind == "discover",
                            Job.state.in_(["queued", "running"]),
                            Job.payload["source_id"].as_string() == source.id,
                        )
                        .limit(1)
                    )
                    if active:
                        continue
                    if source.collection_complete:
                        source.collection_cursor, source.collection_complete = None, False
                    cursor = (
                        int(source.collection_cursor or 0)
                        if account.mode == "mock"
                        else source.collection_cursor
                    )
                    scheduled = at if within_window(at, config) else next_window(at, config)
                    db.add(
                        Job(
                            account_id=account_id,
                            kind="discover",
                            idempotency_key=key,
                            scheduled_at=scheduled,
                            payload={
                                "source_id": source.id,
                                "cursor": cursor,
                                "remaining": min(
                                    source.requested_limit, config["collection_batch_size"]
                                ),
                                "recurring": True,
                                "run_local_day": scheduled.astimezone(ZoneInfo(config["timezone"]))
                                .date()
                                .isoformat(),
                            },
                        )
                    )
                    source.collection_state = "queued"
                    created += 1
    return created
