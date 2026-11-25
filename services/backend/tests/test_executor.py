"""Fake Instagram side effects; all network calls are represented by RequestGuard."""

from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.account_lane import AccountLane
from app.adapters.instagram import RelationshipSnapshot
from app.dashboard import enqueue
from app.execution_errors import OwnershipLost, Restriction, TransientRead, UncertainWrite
from app.executor import Executor
from app.models import Account, AccountSettings, ActionAttempt, BudgetUsage, Job, Relationship, now
from app.schemas import JobCreate, SettingsValues

pytestmark = pytest.mark.skipif(
    not __import__("os").getenv("TEST_POSTGRES_URL"), reason="Requires isolated PostgreSQL schemas"
)


class FakeRemote:
    def __init__(self):
        self.state, self.writes, self.fail = "none", 0, None
        self.before_write = None

    def adapter(self, guard):
        remote = self

        class Adapter:
            def relationship(self, _target_id):
                guard.before_request()
                assert not guard.lane.connection.in_transaction()
                return RelationshipSnapshot(state=remote.state, follows_back=False)

            def follow(self, target_id):
                if remote.before_write:
                    remote.before_write()
                guard.before_request(mutation=True)
                remote.writes += 1
                remote.state = "following"
                if remote.fail == "crash":
                    raise SystemExit("Injected death after remote acceptance")
                if remote.fail == "timeout":
                    raise UncertainWrite("Injected write timeout")
                return self.relationship(target_id)

            def unfollow(self, target_id):
                guard.before_request(mutation=True)
                remote.writes += 1
                remote.state = "none"
                return self.relationship(target_id)

        return Adapter()


def prepare(postgres, requests=20):
    engine, account_id = postgres
    with Session(engine) as db:
        account = db.get(Account, account_id)
        account.mode, account.session_state = "live", "connected"
        account.live_acquisition_enabled = True
        account.live_cleanup_enabled = True
        account.read_only_verified_at = now()
        values = SettingsValues(
            follows_per_day=5, unfollows_per_day=5, request_ceiling=requests
        ).model_dump()
        db.add(AccountSettings(account_id=account_id, values=values))
        job, _ = enqueue(
            db,
            JobCreate(
                account_id=account_id,
                target_id="demo-concurrent",
                kind="follow",
                idempotency_key="executor-reviewed-follow",
            ),
        )
        db.commit()
        return engine, account_id, job.id


@pytest.mark.parametrize("requests", [20, None])
def test_verified_follow_counts_requests_and_preserves_journal(postgres, requests):
    engine, account_id, job_id = prepare(postgres, requests=requests)
    remote = FakeRemote()
    assert Executor(engine, remote.adapter).run_once()
    with Session(engine) as db:
        assert db.get(Job, job_id).state == "succeeded"
        rel = db.scalar(select(Relationship))
        assert rel.managed and rel.ever_followed and rel.observed_state == "following"
        assert rel.follow_confirmed_at is not None
        usage = {x.category: (x.consumed, x.reserved) for x in db.scalars(select(BudgetUsage))}
        assert usage == {"requests": (3, 0), "follows": (1, 0)}
        assert db.scalar(select(ActionAttempt)).request_count == 3
    assert not Executor(engine, remote.adapter).run_once()
    assert remote.writes == 1


def test_budget_reservation_defers_before_any_request(postgres):
    engine, account_id, job_id = prepare(postgres, requests=3)
    remote = FakeRemote()
    assert Executor(engine, remote.adapter).run_once()
    with Session(engine) as db:
        job = db.get(Job, job_id)
        assert job.state == "queued" and job.error == "BUDGET_EXHAUSTED"
        assert job.scheduled_at > now()
        assert db.scalar(select(ActionAttempt)).request_count == 0
    assert remote.writes == 0


def test_protection_change_after_preflight_blocks_write(postgres):
    engine, account_id, job_id = prepare(postgres)
    remote = FakeRemote()

    def protect():
        with Session(engine) as db:
            db.scalar(select(Relationship)).protected = True
            db.commit()

    remote.before_write = protect
    assert Executor(engine, remote.adapter).run_once()
    with Session(engine) as db:
        assert db.get(Job, job_id).state == "skipped"
        assert not db.scalar(select(Relationship)).ever_followed
        assert db.scalar(select(ActionAttempt)).request_count == 1
    assert remote.writes == 0


@pytest.mark.parametrize("failure", ["timeout", "crash"])
def test_uncertain_write_never_repeats_and_can_reconcile(postgres, failure):
    engine, account_id, job_id = prepare(postgres)
    remote = FakeRemote()
    remote.fail = failure
    executor = Executor(engine, remote.adapter)
    if failure == "crash":
        with pytest.raises(SystemExit):
            executor.run_once()
        # A new process owns a new lane. Expire the committed lease to exercise recovery.
        with Session(engine) as db:
            db.get(Job, job_id).lease_expires_at = now() - timedelta(seconds=1)
            db.commit()
        assert not executor.run_once()
    else:
        assert executor.run_once()
    with Session(engine) as db:
        assert db.get(Job, job_id).state == "unknown"
        replay, existing = enqueue(
            db,
            JobCreate(
                account_id=account_id,
                target_id="demo-concurrent",
                kind="follow",
                idempotency_key="different-key-after-timeout",
            ),
        )
        assert existing and replay.id == job_id
        read = Job(
            account_id=account_id,
            target_id="demo-concurrent",
            kind="reconcile",
            idempotency_key="check-unknown-outcome",
            payload={"reconcile_job_id": job_id},
        )
        db.add(read)
        db.commit()
    remote.fail = None
    assert Executor(engine, remote.adapter).run_once()
    assert remote.writes == 1
    with Session(engine) as db:
        assert db.get(Job, job_id).state == "succeeded"
        assert db.scalar(select(Relationship)).ever_followed


def test_account_lane_prevents_a_second_executor(postgres):
    engine, account_id, job_id = prepare(postgres)
    with AccountLane(engine, account_id) as first:
        assert first.acquired
        with AccountLane(engine, account_id) as second:
            assert not second.acquired
        assert not Executor(engine, FakeRemote().adapter).run_once()


def test_swallowed_guard_failure_still_blocks_later_mutation(postgres):
    engine, account_id, job_id = prepare(postgres)
    remote = FakeRemote()

    def factory(guard):
        base = remote.adapter(guard)

        class Adapter:
            def relationship(self, target_id):
                return base.relationship(target_id)

            def follow(self, target_id):
                with Session(engine) as db:
                    db.get(Account, account_id).restricted = True
                    db.commit()
                try:
                    guard.before_request()
                except Restriction:
                    pass  # Models the library's broad precheck exception handler.
                return base.follow(target_id)

        return Adapter()

    assert Executor(engine, factory).run_once()
    assert remote.writes == 0
    with Session(engine) as db:
        assert db.get(Job, job_id).state == "blocked"
        assert db.get(Account, account_id).restricted


def test_undispatched_claim_is_recovered_without_repeating_a_write(postgres):
    engine, account_id, job_id = prepare(postgres)
    remote = FakeRemote()
    executor = Executor(engine, remote.adapter)
    with AccountLane(engine, account_id) as lane:
        with Session(engine) as db, db.begin():
            assert executor.claim(db, account_id, lane.owner)
    with Session(engine) as db:
        db.get(Job, job_id).lease_expires_at = now() - timedelta(seconds=1)
        db.commit()
    assert executor.run_once()
    assert remote.writes == 1
    with Session(engine) as db:
        assert db.get(Job, job_id).attempt_count == 2
        assert sorted(x.outcome for x in db.scalars(select(ActionAttempt))) == [
            "interrupted",
            "succeeded",
        ]


def test_lost_lock_connection_prevents_mutation_and_requires_recovery(postgres):
    engine, account_id, job_id = prepare(postgres)
    remote = FakeRemote()

    def factory(guard):
        remote.before_write = lambda: guard.lane.connection.invalidate()
        return remote.adapter(guard)

    with pytest.raises(OwnershipLost):
        Executor(engine, factory).run_once()
    assert remote.writes == 0
    with Session(engine) as db:
        job = db.get(Job, job_id)
        assert job.state == "running" and job.dispatch_started_at is None
        job.lease_expires_at = now() - timedelta(seconds=1)
        db.commit()
    remote.before_write = None
    assert Executor(engine, remote.adapter).run_once()
    assert remote.writes == 1


def test_negative_reconciliation_keeps_original_intent_blocked(postgres):
    engine, account_id, job_id = prepare(postgres)
    remote = FakeRemote()
    remote.fail = "timeout"
    assert Executor(engine, remote.adapter).run_once()
    remote.state = "none"
    with Session(engine) as db:
        db.add(
            Job(
                account_id=account_id,
                target_id="demo-concurrent",
                kind="reconcile",
                idempotency_key="negative-reconcile",
                payload={"reconcile_job_id": job_id},
            )
        )
        db.commit()
    assert Executor(engine, remote.adapter).run_once()
    with Session(engine) as db:
        assert db.get(Job, job_id).state == "blocked"
        existing, duplicate = enqueue(
            db,
            JobCreate(
                account_id=account_id,
                target_id="demo-concurrent",
                kind="follow",
                idempotency_key="never-blindly-repeat",
            ),
        )
        assert duplicate and existing.id == job_id
    assert remote.writes == 1


def test_a_second_job_cannot_overspend_request_budget(postgres):
    from app.models import Target

    engine, account_id, job_id = prepare(postgres, requests=4)
    remote = FakeRemote()
    assert Executor(engine, remote.adapter).run_once()
    remote.state = "none"
    with Session(engine) as db:
        db.add(Target(id="demo-second", username="second_sample"))
        db.flush()
        db.add(Relationship(account_id=account_id, target_id="demo-second", observed_state="none"))
        db.flush()
        other, _ = enqueue(
            db,
            JobCreate(
                account_id=account_id,
                target_id="demo-second",
                kind="follow",
                idempotency_key="second-budgeted-intent",
            ),
        )
        other_id = other.id
        db.commit()
    assert Executor(engine, remote.adapter).run_once()
    with Session(engine) as db:
        assert db.get(Job, other_id).state == "queued"
        assert db.get(Job, other_id).error == "BUDGET_EXHAUSTED"
        assert (
            db.scalar(select(BudgetUsage).where(BudgetUsage.category == "requests")).consumed == 3
        )
    assert remote.writes == 1


def test_crash_before_transport_releases_reserved_units_on_recovery(postgres):
    engine, account_id, job_id = prepare(postgres)

    def die(_guard):
        raise SystemExit("Injected death after reserving capacity")

    with pytest.raises(SystemExit):
        Executor(engine, die).run_once()
    with Session(engine) as db:
        assert sum(x.reserved for x in db.scalars(select(BudgetUsage))) == 5
        db.get(Job, job_id).lease_expires_at = now() - timedelta(seconds=1)
        db.commit()
    remote = FakeRemote()
    assert Executor(engine, remote.adapter).run_once()
    with Session(engine) as db:
        assert all(x.reserved == 0 for x in db.scalars(select(BudgetUsage)))
    assert remote.writes == 1


def test_read_only_worker_blocks_approved_writes_before_adapter_creation(postgres):
    engine, account_id, job_id = prepare(postgres)

    def forbidden(_guard):
        pytest.fail("Read-only execution must never create a mutation adapter")

    assert Executor(engine, forbidden, allow_mutations=False).run_once()
    with Session(engine) as db:
        job = db.get(Job, job_id)
        assert job.state == "blocked" and job.error == "OPERATION_BLOCKED"
        assert job.dispatch_started_at is None
        assert not list(db.scalars(select(BudgetUsage)))


def test_failed_preflight_releases_only_unsent_intent_for_a_new_owner_review(postgres):
    engine, account_id, job_id = prepare(postgres)

    def factory(guard):
        class Adapter:
            def relationship(self, _target):
                guard.before_request()
                raise TransientRead("Synthetic definite read failure")

        return Adapter()

    assert Executor(engine, factory).run_once()
    with Session(engine) as db:
        original = db.get(Job, job_id)
        assert original.state == "failed" and original.dispatch_started_at is None
        reviewed, duplicate = enqueue(
            db,
            JobCreate(
                account_id=account_id,
                target_id=original.target_id,
                kind="follow",
                idempotency_key="fresh-owner-review-after-read-failure",
            ),
        )
        assert not duplicate and reviewed.id != job_id and reviewed.state == "queued"
        rel = db.scalar(select(Relationship))
        assert not rel.ever_followed and rel.observed_state == "none"


def test_new_lock_owner_waits_for_unexpired_claim_before_any_other_job(postgres):
    engine, account_id, job_id = prepare(postgres)
    executor = Executor(engine, FakeRemote().adapter)
    with AccountLane(engine, account_id) as lane:
        with Session(engine) as db, db.begin():
            assert executor.claim(db, account_id, lane.owner)
        # Simulate loss of the ownership connection while an old request may
        # still be in flight. The HTTP caller is not assumed dead.
        lane.connection.invalidate()
    with Session(engine) as db:
        db.add(
            Job(
                account_id=account_id,
                target_id="demo-concurrent",
                kind="reconcile",
                idempotency_key="another-ready-read",
            )
        )
        db.commit()
    called = False

    def forbidden(_guard):
        nonlocal called
        called = True
        pytest.fail("An unexpired prior claim must hold the entire account lane")

    assert not Executor(engine, forbidden).run_once()
    assert not called
    with Session(engine) as db:
        assert db.get(Job, job_id).state == "running"
        assert (
            db.scalar(select(Job).where(Job.idempotency_key == "another-ready-read")).state
            == "queued"
        )


def test_database_failure_after_remote_acceptance_requires_reconciliation(postgres):
    from sqlalchemy import event
    from sqlalchemy.exc import OperationalError

    engine, account_id, job_id = prepare(postgres)
    remote = FakeRemote()
    injected = False

    def fail_observation(_conn, _cursor, statement, parameters, _context, _many):
        nonlocal injected
        if remote.writes and not injected and statement.startswith("UPDATE relationships"):
            injected = True
            raise OperationalError(statement, parameters, RuntimeError("Injected database failure"))

    event.listen(engine, "before_cursor_execute", fail_observation)
    try:
        assert Executor(engine, remote.adapter).run_once()
    finally:
        event.remove(engine, "before_cursor_execute", fail_observation)
    assert injected and remote.writes == 1
    with Session(engine) as db:
        job = db.get(Job, job_id)
        assert job.state == "unknown" and job.dispatch_started_at is not None
        assert db.scalar(select(Relationship)).observed_state == "none"
        db.add(
            Job(
                account_id=account_id,
                target_id=job.target_id,
                kind="reconcile",
                idempotency_key="after-database-failure",
                payload={"reconcile_job_id": job_id},
            )
        )
        db.commit()
    assert Executor(engine, remote.adapter).run_once()
    assert not Executor(engine, remote.adapter).run_once()
    with Session(engine) as db:
        assert db.get(Job, job_id).state == "succeeded"
        rel = db.scalar(select(Relationship))
        assert rel.managed and rel.ever_followed and rel.observed_state == "following"
    assert remote.writes == 1
