import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.dashboard import mock_collect
from app.models import Account, AccountSettings, Job, Relationship, Source, now
from app.schemas import SettingsValues
from app.source_schedules import materialize_collections
from app.timing import local_day, scheduled_instant


def test_schedule_requires_version_and_disable_cancels_only_recurring(system):
    client, db, account, rel = system
    source = Source(account_id=account.id, kind="profile_followers", value="scheduled.example")
    db.add(source)
    db.flush()
    recurring = Job(
        account_id=account.id,
        kind="discover",
        idempotency_key="recurring-test",
        payload={"source_id": source.id, "recurring": True},
    )
    manual = Job(
        account_id=account.id,
        kind="discover",
        idempotency_key="manual-test",
        payload={"source_id": source.id},
    )
    db.add_all([recurring, manual])
    db.commit()
    path = f"/api/v1/sources/{source.id}/schedule"
    assert source.schedule_enabled is False
    assert (
        client.patch(
            path, json={"expected_version": 1, "enabled": True, "local_time": "24:00"}
        ).status_code
        == 422
    )
    enabled = client.patch(
        path, json={"expected_version": 1, "enabled": True, "local_time": "10:30"}
    )
    assert enabled.status_code == 200 and enabled.json()["schedule_version"] == 2
    assert (
        client.patch(
            path, json={"expected_version": 1, "enabled": False, "local_time": "10:30"}
        ).status_code
        == 409
    )
    assert (
        client.patch(
            path, json={"expected_version": 2, "enabled": False, "local_time": "10:30"}
        ).status_code
        == 200
    )
    db.refresh(recurring)
    db.refresh(manual)
    assert recurring.state == "cancelled" and recurring.error == "schedule_disabled"
    assert manual.state == "queued"


def test_recurring_simulation_keeps_schedule_metadata_and_never_touches_history(system):
    _, db, account, _ = system
    source = Source(
        account_id=account.id,
        kind="profile_followers",
        value="scheduled.example",
        schedule_enabled=True,
    )
    db.add(source)
    db.flush()
    day = local_day(now(), SettingsValues().model_dump())
    job = Job(
        account_id=account.id,
        kind="discover",
        idempotency_key="scheduled-test",
        payload={
            "source_id": source.id,
            "recurring": True,
            "run_local_day": day,
            "remaining": 20,
            "cursor": 0,
        },
    )
    db.add(job)
    db.flush()
    assert mock_collect(db, job)[0] == "simulated"
    db.flush()
    continuation = db.scalar(select(Job).where(Job.id != job.id))
    assert continuation.payload["recurring"] and continuation.payload["run_local_day"] == day
    source.schedule_enabled = False
    assert mock_collect(db, continuation)[0] == "skipped"
    for rel in db.scalars(select(Relationship)):
        assert not rel.ever_followed and rel.observed_state == "none"


def test_daily_time_handles_gap_and_first_fold():
    assert scheduled_instant(date(2026, 3, 8), "02:30", "America/Toronto") == datetime(
        2026, 3, 8, 7, 0, tzinfo=UTC
    )
    assert scheduled_instant(date(2026, 11, 1), "01:30", "America/Toronto") == datetime(
        2026, 11, 1, 5, 30, tzinfo=UTC
    )


pg = pytest.mark.skipif(
    not os.getenv("TEST_POSTGRES_URL"), reason="Requires isolated PostgreSQL schema"
)


def prepare(postgres, **values):
    engine, account_id = postgres
    with Session(engine) as db:
        account = db.get(Account, account_id)
        account.paused = True
        source = Source(
            account_id=account_id,
            kind="profile_followers",
            value="scheduled.example",
            schedule_enabled=True,
            schedule_local_time="01:30",
            **values,
        )
        db.add(source)
        db.commit()
        source_id = source.id
    return engine, account_id, source_id


@pg
def test_restart_and_duplicate_schedulers_make_one_daily_job(postgres):
    engine, account_id, source_id = prepare(postgres)
    first = datetime(2026, 11, 1, 5, 30, tzinfo=UTC)
    with ThreadPoolExecutor(max_workers=2) as pool:
        counts = list(
            pool.map(lambda _: materialize_collections(engine, clock=lambda: first), range(2))
        )
    assert sum(counts) == 1
    assert materialize_collections(engine, clock=lambda: first + timedelta(hours=1)) == 0
    with Session(engine) as db:
        job = db.scalar(select(Job))
        assert job.payload["cursor"] == 0 and job.payload["remaining"] == 20
        assert job.payload["run_local_day"] == "2026-11-01"
        source = db.get(Source, source_id)
        source.collection_cursor = "10"
        db.commit()
    # Restart after days paused expires the older run and retains its checkpoint.
    later = first + timedelta(days=4, hours=2)
    assert materialize_collections(engine, clock=lambda: later) == 1
    assert materialize_collections(engine, clock=lambda: later) == 0
    with Session(engine) as db:
        jobs = db.scalars(select(Job)).all()
        assert len(jobs) == 2
        assert sum(job.state == "cancelled" for job in jobs) == 1
        assert next(job for job in jobs if job.state == "queued").payload["cursor"] == 10


@pg
def test_live_schedules_require_connection_and_transport_permission(postgres):
    engine, account_id, source_id = prepare(postgres)
    at = datetime(2026, 11, 1, 8, 0, tzinfo=UTC)
    with Session(engine) as db:
        account = db.get(Account, account_id)
        account.mode, account.session_state = "live", "disconnected"
        db.commit()
    assert materialize_collections(engine, clock=lambda: at, allow_live=True) == 0
    with Session(engine) as db:
        db.get(Account, account_id).session_state = "connected"
        db.commit()
    assert materialize_collections(engine, clock=lambda: at) == 0
    assert materialize_collections(engine, clock=lambda: at, allow_live=True) == 1


@pg
def test_window_on_following_day_is_not_expired_before_it_runs(postgres):
    engine, account_id, source_id = prepare(postgres)
    with Session(engine) as db:
        db.get(Source, source_id).schedule_local_time = "23:00"
        db.add(
            AccountSettings(
                account_id=account_id,
                values=SettingsValues(window_start="09:00", window_end="17:00").model_dump(),
            )
        )
        db.commit()
    at = datetime(2026, 11, 2, 4, 0, tzinfo=UTC)  # Nov 1, 23:00 local.
    assert materialize_collections(engine, clock=lambda: at) == 1
    assert materialize_collections(engine, clock=lambda: at + timedelta(minutes=1)) == 0
    with Session(engine) as db:
        job = db.scalar(select(Job))
        assert job.state == "queued" and job.payload["run_local_day"] == "2026-11-02"
        assert job.scheduled_at == datetime(2026, 11, 2, 14, 0, tzinfo=UTC)
