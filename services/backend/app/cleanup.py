"""Cleanup scheduling is intent creation only; fresh policy is checked at dispatch."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from .account_lane import AccountLane
from .cleanup_policy import cleanup_due
from .dashboard import account_settings, fingerprint
from .models import Account, Job, Relationship, now
from .timing import local_day, next_window, within_window


def materialize_cleanup(engine, clock=now):
    with Session(engine) as db:
        ids = db.scalars(
            select(Account.id).where(Account.mode == "live", Account.live_cleanup_enabled.is_(True))
        ).all()
    created = 0
    for account_id in ids:
        with AccountLane(engine, account_id) as lane:
            if not lane.acquired:
                continue
            at = clock()
            with Session(engine) as db, db.begin():
                account = db.scalar(
                    select(Account).where(Account.id == account_id).with_for_update()
                )
                if not account.live_cleanup_enabled:
                    continue
                config = account_settings(db, account_id)
                for rel in db.scalars(
                    select(Relationship)
                    .where(
                        Relationship.account_id == account_id,
                        Relationship.managed.is_(True),
                        Relationship.observed_state == "following",
                    )
                    .with_for_update()
                ):
                    due = cleanup_due(rel, config)
                    rel.unfollow_due_at = due
                    outstanding = db.scalar(
                        select(Job)
                        .where(
                            Job.account_id == account_id,
                            Job.target_id == rel.target_id,
                            Job.kind == "unfollow",
                            Job.state.in_(["queued", "running", "unknown", "blocked"]),
                        )
                        .limit(1)
                    )
                    if outstanding:
                        if (
                            outstanding.state == "queued"
                            and outstanding.payload.get("cleanup")
                            and due
                        ):
                            due_stamp = due.isoformat()
                            if outstanding.payload.get("retention_due") != due_stamp:
                                earliest = max(at, due)
                                outstanding.scheduled_at = (
                                    earliest
                                    if within_window(earliest, config)
                                    else next_window(earliest, config)
                                )
                                outstanding.payload = {
                                    **outstanding.payload,
                                    "retention_due": due_stamp,
                                }
                        continue
                    if due is None or due > at or rel.protected or rel.excluded:
                        continue
                    key = "cleanup:" + fingerprint(
                        {
                            "relationship": rel.id,
                            "anchor": (rel.adopted_at or rel.follow_confirmed_at).isoformat(),
                            "local_day": local_day(at, config),
                        }
                    )
                    if db.scalar(
                        select(Job.id).where(
                            Job.account_id == account_id, Job.idempotency_key == key
                        )
                    ):
                        continue
                    scheduled = at if within_window(at, config) else next_window(at, config)
                    db.add(
                        Job(
                            account_id=account_id,
                            target_id=rel.target_id,
                            kind="unfollow",
                            priority=1,
                            scheduled_at=scheduled,
                            idempotency_key=key,
                            payload={"cleanup": True, "retention_due": due.isoformat()},
                        )
                    )
                    created += 1
    return created
