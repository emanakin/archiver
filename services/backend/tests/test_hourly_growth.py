from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.dashboard import account_settings
from app.models import (
    AccountSettings,
    BudgetUsage,
    Discovery,
    FollowIntent,
    Job,
    Relationship,
    Source,
    Target,
)
from app.ranked_follows import hourly_slots, queue_ranked_cohort
from app.schemas import SettingsValues


def prepare_pool(db, account, count=110):
    account.instagram_id = "42"
    account.mode, account.session_state = "live", "connected"
    account.live_acquisition_enabled = True
    account.read_only_verified_at = datetime(2026, 11, 23, tzinfo=UTC)
    config = SettingsValues(
        follow_pacing="hourly",
        follows_per_day=None,
        request_ceiling=None,
        ranked_follow_mode="eligible",
    ).model_dump()
    db.add(AccountSettings(account_id=account.id, values=config))
    source = Source(account_id=account.id, kind="profile_followers", value="reference")
    db.add(source)
    db.flush()
    for index in range(count):
        target = Target(id=f"hourly-{index}", username=f"person{index}")
        db.add(target)
        db.flush()
        db.add_all(
            [
                Discovery(source_id=source.id, target_id=target.id),
                Relationship(
                    account_id=account.id,
                    target_id=target.id,
                    observed_state="none",
                    follows_back=False,
                ),
            ]
        )
    db.flush()
    return config


def test_hourly_plan_is_distributed_varied_and_restart_stable():
    config = SettingsValues().model_dump()
    hour = datetime(2026, 11, 1, 5, tzinfo=UTC)  # Toronto fall-back hour
    counts = set()
    for offset in range(48):
        at = hour + timedelta(hours=offset)
        slots = hourly_slots("archive", at, config)
        assert slots == hourly_slots("archive", at + timedelta(minutes=20), config)
        assert 75 <= len(slots) <= 100
        counts.add(len(slots))
        times = [due for _, due, _ in slots]
        assert times == sorted(times)
        assert times[0] < at + timedelta(minutes=1)
        assert times[-1] > at + timedelta(minutes=59)
        intervals = [(b - a).total_seconds() for a, b in zip(times, times[1:], strict=False)]
        assert min(intervals) > 10
        assert len({round(x, 2) for x in intervals}) > 20
    assert len(counts) > 10


def test_full_hour_has_no_daily_cap_duplicate_slots_or_backlog(system):
    _, db, account, _ = system
    config = prepare_pool(db, account)
    at = datetime(2026, 11, 23, 12, tzinfo=UTC)
    db.add(
        BudgetUsage(
            account_id=account.id,
            local_day="2026-11-23",
            category="follows",
            consumed=2000,
            reserved=0,
        )
    )
    slots = hourly_slots(account.id, at, config)
    for key, due, expires in slots:
        queue_ranked_cohort(db, account.id, due)
        db.flush()
        queued = db.scalars(select(Job).where(Job.state == "queued")).all()
        assert len(queued) == 1
        job = queued[0]
        assert job.payload["ranked_slot"] == key
        assert job.payload["ranked_expires_at"] == expires.isoformat()
        queue_ranked_cohort(db, account.id, due)
        job.state, job.dispatch_started_at = "succeeded", due
        db.flush()
    assert len(db.scalars(select(Job)).all()) == len(slots)
    # A restart several hours later plans only the current hour.
    later = at + timedelta(hours=5, minutes=30)
    queue_ranked_cohort(db, account.id, later)
    db.flush()
    queued = db.scalar(select(Job).where(Job.state == "queued"))
    assert queued.payload["ranked_slot"].startswith(later.replace(minute=0).isoformat())


def test_unlimited_settings_roundtrip_and_invalid_hourly_ranges(system):
    client, db, account, _ = system
    config = account_settings(db, account.id)
    version = config.pop("version")
    config.update(
        follows_per_day=None,
        follow_pacing="hourly",
        follows_per_hour_min=75,
        follows_per_hour_max=100,
    )

    path = f"/api/v1/accounts/{account.id}/settings"
    response = client.patch(path, json={**config, "expected_version": version})
    assert response.status_code == 200
    assert client.get(path).json()["follows_per_day"] is None
    with pytest.raises(ValidationError):
        SettingsValues(follows_per_hour_min=101, follows_per_hour_max=100)
    with pytest.raises(ValidationError):
        SettingsValues(follows_per_day=None, follow_pacing="daily")
    assert (
        client.get(f"/api/v1/accounts/{account.id}/follow-telemetry").json()["follow_dispatches"]
        == 0
    )


def test_lowering_pace_releases_only_unsent_ranked_follow(system):
    client, db, account, _ = system
    previous = prepare_pool(db, account, count=2)
    at = datetime(2026, 11, 23, 12, tzinfo=UTC)
    queue_ranked_cohort(db, account.id, at)
    auto = db.scalar(select(Job).where(Job.state == "queued"))
    auto_id = auto.id
    assert db.scalar(select(FollowIntent).where(FollowIntent.job_id == auto_id))
    db.add(Job(account_id=account.id, kind="follow", idempotency_key="manual-keep"))
    db.add(
        BudgetUsage(
            account_id=account.id,
            local_day="2026-11-23",
            category="follows",
            consumed=21,
            reserved=0,
        )
    )
    db.commit()
    response = client.patch(
        f"/api/v1/accounts/{account.id}/settings",
        json={
            **previous,
            "expected_version": 1,
            "follows_per_day": 20,
            "follows_per_hour_min": 5,
            "follows_per_hour_max": 5,
        },
    )
    assert response.status_code == 200
    db.expire_all()
    assert db.get(Job, auto_id).state == "cancelled"
    assert not db.scalar(select(FollowIntent).where(FollowIntent.job_id == auto_id))
    manual = db.scalar(select(Job).where(Job.idempotency_key == "manual-keep"))
    assert manual.state == "queued"
    manual.state = "cancelled"
    db.flush()
    queue_ranked_cohort(db, account.id, at)
    assert not db.scalar(select(Job).where(Job.state == "queued"))
