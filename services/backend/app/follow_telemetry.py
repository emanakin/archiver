"""Account-scoped timing evidence, with no library response or session data."""

from datetime import UTC, timedelta

from sqlalchemy import func, select

from .models import ActionAttempt, AttemptEvent, Job


def follow_telemetry(db, account_id, at):
    start = at - timedelta(hours=1)
    attempts = db.execute(
        select(ActionAttempt, Job.kind)
        .join(Job, Job.id == ActionAttempt.job_id)
        .where(
            Job.account_id == account_id,
            ActionAttempt.completed_at >= start,
            ActionAttempt.completed_at <= at,
        )
        .order_by(ActionAttempt.started_at)
    ).all()
    dispatches = db.scalars(
        select(Job.dispatch_started_at)
        .where(
            Job.account_id == account_id,
            Job.kind == "follow",
            Job.dispatch_started_at >= start,
            Job.dispatch_started_at <= at,
        )
        .order_by(Job.dispatch_started_at)
    ).all()
    dispatches = [x.replace(tzinfo=UTC) if x.tzinfo is None else x for x in dispatches]
    return {
        "window_start": start.isoformat(),
        "observed_at": at.isoformat(),
        "follow_dispatches": len(dispatches),
        "follow_successes": sum(
            kind == "follow" and item.outcome == "succeeded" for item, kind in attempts
        ),
        "requests": db.scalar(
            select(func.count())
            .select_from(AttemptEvent)
            .join(ActionAttempt, ActionAttempt.id == AttemptEvent.attempt_id)
            .where(
                ActionAttempt.account_id == account_id,
                AttemptEvent.event == "request_started",
                AttemptEvent.created_at >= start,
                AttemptEvent.created_at <= at,
            )
        ),
        "errors": sum(item.error_code is not None for item, _ in attempts),
        "dispatch_times": [x.isoformat() for x in dispatches],
        "interval_seconds": [
            (b - a).total_seconds() for a, b in zip(dispatches, dispatches[1:], strict=False)
        ],
    }


def record_follow_telemetry(db, account_id, attempt_id, at, code, failure_kind=None):
    db.flush()
    db.add(
        AttemptEvent(
            attempt_id=attempt_id,
            event="follow_timing_snapshot",
            detail={
                **follow_telemetry(db, account_id, at),
                "code": code,
                "failure_kind": failure_kind,
            },
            created_at=at,
        )
    )
