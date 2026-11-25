import importlib.util
import os
from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.adapters.guarded_client import translate
from app.adapters.instagram import RelationshipSnapshot
from app.executor import Executor
from app.models import Account, AccountSettings, AttemptEvent, BudgetUsage, Job, Source, now


def test_timeout_is_identified_without_recording_exception_text():
    failure = translate(TimeoutError("private transport details"), mutation=True)
    assert failure.code == "OUTCOME_UNKNOWN"
    assert failure.failure_kind == "timeout"
    assert "private" not in str(failure)


@pytest.mark.skipif(not os.getenv("TEST_POSTGRES_URL"), reason="Requires PostgreSQL")
@pytest.mark.parametrize("expired", [True, False])
def test_expired_slot_stops_write_but_allows_late_confirmation(postgres, expired):
    from test_executor import prepare

    engine, account_id, job_id = prepare(postgres, requests=None)
    at = now()
    clock = [at]
    with Session(engine) as db, db.begin():
        stored = db.get(AccountSettings, account_id)
        stored.values = {**stored.values, "follow_pacing": "hourly", "follows_per_day": None}
        job = db.get(Job, job_id)
        job.payload = {
            "ranked_expires_at": (at + timedelta(seconds=-1 if expired else 1)).isoformat()
        }
        db.add(
            BudgetUsage(
                account_id=account_id,
                local_day=at.astimezone(ZoneInfo("America/Toronto")).date().isoformat(),
                category="follows",
                consumed=2000,
            )
        )

    class Adapter:
        def __init__(self, guard):
            self.guard = guard
            self.state = "none"

        def relationship(self, _):
            self.guard.before_request()
            return RelationshipSnapshot(state=self.state, follows_back=False)

        def follow(self, target):
            self.guard.before_request(mutation=True)
            self.state = "following"
            clock[0] += timedelta(seconds=30)
            return self.relationship(target)

    assert Executor(engine, Adapter, clock=lambda: clock[0]).run_once()
    with Session(engine) as db:
        job = db.get(Job, job_id)
        assert job.state == ("skipped" if expired else "succeeded")
        assert (job.dispatch_started_at is None) == expired
        snapshot = db.scalar(
            select(AttemptEvent).where(AttemptEvent.event == "follow_timing_snapshot")
        )
        assert snapshot.detail["follow_dispatches"] == (0 if expired else 1)
        assert snapshot.detail["requests"] == (0 if expired else 3)


@pytest.mark.skipif(not os.getenv("TEST_POSTGRES_URL"), reason="Requires PostgreSQL")
def test_growth_setup_allocates_1000_daily_samples_and_tops_up_once(postgres, monkeypatch):
    script = Path(os.getenv("ARCHIVE_SCRIPTS_DIR", "../../scripts")) / "set_growth_protocol.py"
    spec = importlib.util.spec_from_file_location("growth_protocol", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    engine, account_id = postgres
    monkeypatch.setattr(module, "SessionLocal", sessionmaker(engine))
    with Session(engine) as db, db.begin():
        account = db.get(Account, account_id)
        account.mode, account.live_acquisition_enabled = "live", True
        db.add(AccountSettings(account_id=account_id, values={"collection_batch_size": 20}))
        for index in range(34):
            source = Source(
                account_id=account_id,
                kind="profile_followers",
                value=f"source{index}",
                schedule_enabled=True,
                requested_limit=10,
            )
            db.add(source)
            db.flush()
            if index == 0:
                db.add(
                    Job(
                        account_id=account_id,
                        kind="discover",
                        state="succeeded",
                        idempotency_key=f"daily-collection:{source.id}:{now().astimezone(ZoneInfo('America/Toronto')).date()}",
                    )
                )
    result = module.apply("concurrency_demo")
    assert result["daily_sample_size"] == 1000
    assert result["settings"]["follows_per_day"] is None
    assert result["settings"]["follows_per_hour_min"] == 75
    module.apply("concurrency_demo")
    with Session(engine) as db:
        assert sum(s.requested_limit for s in db.scalars(select(Source))) == 1000
        assert (
            len(db.scalars(select(Job).where(Job.idempotency_key.like("growth-sample:%"))).all())
            == 1
        )
