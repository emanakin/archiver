from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app import dm_intake
from app.models import AccountSettings, DmInbox, Job, now
from app.schemas import SettingsValues


@pytest.mark.parametrize("thread", [None, "123"])
def test_failed_polls_back_off_then_stop_without_blocking_healthy_senders(
    system, monkeypatch, thread
):
    _, db, account, _ = system
    monkeypatch.setattr(dm_intake, "settings", lambda: SimpleNamespace(dm_poll_interval_seconds=60))
    account.mode, account.session_state = "live", "connected"
    at = now() - timedelta(hours=1)
    bad = DmInbox(
        account_id=account.id,
        sender_id="555",
        sender_username="missing",
        thread_id=thread,
        direct_publish_from=at,
    )
    good = DmInbox(
        account_id=account.id,
        sender_id="666",
        sender_username="healthy",
        thread_id="456",
        direct_publish_from=at,
    )
    db.add_all(
        [
            bad,
            good,
            AccountSettings(
                account_id=account.id, values=SettingsValues(request_ceiling=None).model_dump()
            ),
        ]
    )
    db.flush()
    latest = None
    for index in range(3):
        created = at + timedelta(minutes=index * 20 + 1)
        latest = Job(
            account_id=account.id,
            kind="dm_poll",
            state="failed",
            error="READ_FAILED",
            idempotency_key=f"failed-inbox-{index}",
            created_at=created,
            completed_at=created + timedelta(seconds=15),
            payload={"sender_id": bad.sender_id, "automatic": True},
        )
        db.add(latest)
        db.flush()
        status = dm_intake.poll_status(db, bad, latest.completed_at, 60)
        assert status["consecutive_poll_failures"] == index + 1
        if index < 2:
            assert status["poll_state"] == "backoff"
            assert status["next_poll_at"] == latest.completed_at + timedelta(minutes=5 * 2**index)
        else:
            assert status["poll_state"] == "needs_attention" and status["next_poll_at"] is None
    db.commit()
    dm_intake.materialize_direct_polls(db.get_bind(), now)
    db.expire_all()
    queued = db.scalars(select(Job).where(Job.state == "queued")).all()
    assert len(queued) == 1 and queued[0].payload["sender_id"] == good.sender_id
    # A single successful manual read resets the failure streak and resumes cadence.
    db.add(
        Job(
            account_id=account.id,
            kind="dm_poll",
            state="succeeded",
            idempotency_key="manual-recovery",
            created_at=now(),
            completed_at=now(),
            payload={"sender_id": bad.sender_id},
        )
    )
    db.flush()
    assert dm_intake.poll_status(db, bad, now(), 60)["poll_state"] == "scheduled"


def test_backoff_deadline_is_honored_and_existing_thread_inaccessible_requires_review(
    system, monkeypatch
):
    _, db, account, _ = system
    monkeypatch.setattr(dm_intake, "settings", lambda: SimpleNamespace(dm_poll_interval_seconds=60))
    account.mode, account.session_state = "live", "connected"
    at = now() - timedelta(hours=1)
    inbox = DmInbox(
        account_id=account.id, sender_id="555", sender_username="new", direct_publish_from=at
    )
    db.add_all(
        [
            inbox,
            AccountSettings(
                account_id=account.id, values=SettingsValues(request_ceiling=None).model_dump()
            ),
        ]
    )
    job = Job(
        account_id=account.id,
        kind="dm_poll",
        state="failed",
        error="READ_FAILED",
        idempotency_key="first-missing-thread",
        created_at=at + timedelta(seconds=10),
        completed_at=at + timedelta(seconds=20),
        payload={"sender_id": "555"},
    )
    db.add(job)
    db.commit()
    dm_intake.materialize_direct_polls(db.get_bind(), lambda: at + timedelta(seconds=319))
    assert len(db.scalars(select(Job)).all()) == 1
    db.commit()
    dm_intake.materialize_direct_polls(db.get_bind(), lambda: at + timedelta(seconds=320))
    assert len(db.scalars(select(Job)).all()) == 2
    db.rollback()
    inbox.thread_id = "123"
    latest = db.scalar(select(Job).where(Job.state == "queued"))
    latest.state, latest.error, latest.completed_at = "failed", "SOURCE_INACCESSIBLE", now()
    db.flush()
    assert dm_intake.poll_status(db, inbox, now(), 60)["poll_state"] == "needs_attention"


@pytest.mark.parametrize("code", ["DM_CONVERSATION_MISSING", "SOURCE_INACCESSIBLE"])
def test_missing_conversation_waits_for_manual_success(system, monkeypatch, code):
    _, db, account, _ = system
    monkeypatch.setattr(dm_intake, "settings", lambda: SimpleNamespace(dm_poll_interval_seconds=60))
    account.mode, account.session_state = "live", "connected"
    at = now()
    inbox = DmInbox(
        account_id=account.id,
        sender_id="555",
        sender_username="new",
        direct_publish_from=at - timedelta(hours=1),
    )
    db.add_all(
        [
            inbox,
            AccountSettings(
                account_id=account.id, values=SettingsValues(request_ceiling=None).model_dump()
            ),
        ]
    )
    db.add(
        Job(
            account_id=account.id,
            kind="dm_poll",
            state="failed",
            error=code,
            idempotency_key="missing-conversation",
            completed_at=at,
            created_at=at,
            payload={"sender_id": "555"},
        )
    )
    db.commit()
    later = at + timedelta(hours=5)
    dm_intake.materialize_direct_polls(db.get_bind(), lambda: later)
    assert len(db.scalars(select(Job)).all()) == 1
    assert dm_intake.poll_status(db, inbox, later, 60)["poll_state"] == "needs_conversation"
