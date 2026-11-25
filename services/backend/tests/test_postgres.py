"""Real migrations/locking tests, isolated in a random PostgreSQL schema."""

import os
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.dashboard import enqueue
from app.models import FollowIntent, Job, Relationship
from app.schemas import JobCreate
from app.worker import tick

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_POSTGRES_URL"), reason="Set TEST_POSTGRES_URL or use compose.test.yaml"
)


@pytest.mark.parametrize("same_key", [True, False])
def test_concurrent_follow_intents_share_one_job(postgres, same_key):
    engine, account_id = postgres
    barrier = threading.Barrier(2)

    def submit(index):
        with Session(engine, expire_on_commit=False) as db:
            barrier.wait(timeout=10)
            job, existing = enqueue(
                db,
                JobCreate(
                    account_id=account_id,
                    target_id="demo-concurrent",
                    kind="follow",
                    idempotency_key="same-intent" if same_key else f"separate-intent-{index}",
                ),
            )
            db.commit()
            return job.id, existing

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, (0, 1)))
    assert results[0][0] == results[1][0]
    assert sorted(x[1] for x in results) == [False, True]
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(Job)) == 1
        assert db.scalar(select(func.count()).select_from(FollowIntent)) == 1


def test_protection_and_history_survive_new_sessions(postgres):
    engine, account_id = postgres
    with Session(engine) as db:
        job, _ = enqueue(
            db,
            JobCreate(
                account_id=account_id,
                target_id="demo-concurrent",
                kind="follow",
                idempotency_key="protect-before-dispatch",
            ),
        )
        job_id = job.id
        db.commit()
    with Session(engine) as db:
        rel = db.scalar(select(Relationship))
        rel.protected = True
        rel.version += 1
        db.commit()
    with Session(engine) as db:
        assert tick(db)
    engine.dispose()  # Exercise a new pool/connection, as after process restart.
    with Session(engine) as db:
        assert db.get(Job, job_id).state == "skipped"
        rel = db.scalar(select(Relationship))
        assert rel.protected and rel.version == 2
        assert not rel.ever_followed and rel.follow_confirmed_at is None
