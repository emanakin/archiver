import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.executor import Executor
from app.insight_schemas import InsightItem, InsightObservation
from app.insight_watches import materialize
from app.models import Account, AccountSettings, ActionAttempt, InsightWatch, Job, LikerObservation
from app.schemas import SettingsValues


def test_post_watch_is_paused_by_default_and_versioned(system):
    client, db, account, _ = system
    account.mode, account.session_state = "live", "connected"
    db.commit()
    response = client.post(
        "/api/v1/insight-watches",
        json={"account_id": account.id, "target": "https://instagram.com/p/ABC/"},
    )
    assert response.status_code == 201 and not response.json()["enabled"]
    item = response.json()
    path = f"/api/v1/insight-watches/{item['id']}"
    change = {"expected_version": 1, "enabled": True, "interval_minutes": 60, "sample_size": 3}
    assert client.patch(path, json=change).json()["enabled"]
    assert client.patch(path, json=change).status_code == 409
    assert client.get(f"/api/v1/insight-watches?account_id={account.id}").json()["total"] == 1
    assert client.get(path + "/observations").json()["items"] == []
    assert db.scalar(select(Job)) is None
    assert (
        client.post(
            "/api/v1/insight-watches",
            json={"account_id": account.id, "target": "http://127.0.0.1/p/ABC/"},
        ).status_code
        == 422
    )


@pytest.mark.skipif(
    not os.getenv("TEST_POSTGRES_URL"), reason="Requires isolated PostgreSQL account lane"
)
def test_polling_has_no_backlog_or_duplicate_and_retains_first_seen_without_inventing_unlikes(
    postgres,
):
    engine, account_id = postgres
    clock = SimpleNamespace(at=datetime(2026, 11, 18, 12, tzinfo=UTC), users=["100"])
    with Session(engine) as db, db.begin():
        account = db.get(Account, account_id)
        account.mode, account.session_state = "live", "connected"
        db.add(
            AccountSettings(
                account_id=account_id, values=SettingsValues(request_ceiling=100).model_dump()
            )
        )
        db.add(
            InsightWatch(
                id="watch-one",
                account_id=account_id,
                target="https://www.instagram.com/p/ABC/",
                enabled=True,
                sample_size=3,
                interval_minutes=60,
                next_due_at=clock.at,
            )
        )

    class Adapter:
        def __init__(self, guard):
            self.guard = guard

        def inspect(self, *args, **kwargs):
            self.guard.before_request()
            return InsightObservation(
                items=[
                    InsightItem(id=value, kind="user", username=f"observed_{value}")
                    for value in clock.users
                ],
                complete=False,
                note="Partial liker snapshot",
            )

    executor = Executor(engine, Adapter, clock=lambda: clock.at, allow_mutations=False)
    materialize(engine, clock=lambda: clock.at)
    materialize(engine, clock=lambda: clock.at)
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(Job)) == 1
    assert executor.run_account(account_id)
    with Session(engine) as db:
        first = db.get(LikerObservation, ("watch-one", "100"))
        assert first.baseline and first.first_seen_at == clock.at
    clock.at += timedelta(days=2)
    clock.users = ["200"]
    materialize(engine, clock=lambda: clock.at)
    materialize(engine, clock=lambda: clock.at)
    assert executor.run_account(account_id)
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(Job)) == 2
        assert not db.get(LikerObservation, ("watch-one", "200")).baseline
        assert db.get(LikerObservation, ("watch-one", "100")).first_seen_at < clock.at
        assert db.get(LikerObservation, ("watch-one", "100")).last_seen_at < clock.at
    clock.at += timedelta(minutes=61)
    materialize(engine, clock=lambda: clock.at)
    with Session(engine) as db, db.begin():
        db.get(InsightWatch, "watch-one").enabled = False
    assert executor.run_account(account_id)
    with Session(engine) as db:
        latest = db.scalar(select(Job).order_by(Job.created_at.desc(), Job.id.desc()))
        assert latest.state == "skipped"
        assert (
            db.scalar(select(ActionAttempt.request_count).where(ActionAttempt.job_id == latest.id))
            == 0
        )
