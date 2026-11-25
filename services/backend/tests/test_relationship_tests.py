import os
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import relationship_tests as tests_api
from app.adapters.instagram import RelationshipSnapshot
from app.execution_errors import UncertainWrite
from app.executor import Executor
from app.models import (
    Account,
    AccountSettings,
    InsightCheck,
    Job,
    Relationship,
    RelationshipTest,
    now,
)
from app.schemas import SettingsValues


def baseline(db, account_id, state="none"):
    account = db.get(Account, account_id)
    account.mode, account.session_state, account.instagram_id = "live", "connected", "999"
    account.read_only_verified_at = now()
    db.add(
        AccountSettings(
            account_id=account_id,
            values=SettingsValues(
                request_ceiling=100, follows_per_day=1, unfollows_per_day=1
            ).model_dump(),
        )
    )
    job = Job(
        account_id=account_id, kind="insight", state="succeeded", idempotency_key="test-baseline"
    )
    db.add(job)
    db.flush()
    check = InsightCheck(
        account_id=account_id,
        job_id=job.id,
        kind="relationship",
        target="example",
        requested_limit=1,
        observed_at=now(),
        profile={"id": "123", "username": "example", "is_private": False},
        relationship={"target_id": "123", "state": state, "follows_back": False},
    )
    db.add(check)
    db.commit()
    return check.id


def test_existing_follow_cannot_become_a_fresh_test(system):
    client, db, account, _ = system
    check_id = baseline(db, account.id, state="following")
    response = client.post(
        "/api/v1/relationship-tests", json={"account_id": account.id, "check_id": check_id}
    )
    assert response.status_code == 409 and db.scalar(select(RelationshipTest)) is None


def test_preparing_does_not_send_or_enable_actions_and_read_only_cannot_approve(
    system, monkeypatch
):
    client, db, account, _ = system
    monkeypatch.setattr(tests_api, "settings", lambda: SimpleNamespace(adapter_mode="read_only"))
    check_id = baseline(db, account.id)
    response = client.post(
        "/api/v1/relationship-tests", json={"account_id": account.id, "check_id": check_id}
    )
    assert response.status_code == 201 and response.json()["state"] == "prepared"
    assert response.json()["follow_job_id"] is None
    assert (
        client.post(
            f"/api/v1/relationship-tests/{response.json()['id']}/approve", json={}
        ).status_code
        == 409
    )
    assert not account.live_acquisition_enabled and not account.live_cleanup_enabled


@pytest.mark.skipif(
    not os.getenv("TEST_POSTGRES_URL"), reason="Requires isolated PostgreSQL account lane"
)
@pytest.mark.parametrize("outcome", ["restored", "protected", "followback", "unknown", "readonly"])
def test_scoped_pair_preserves_policy_history_and_unknown_outcomes(postgres, monkeypatch, outcome):
    engine, account_id = postgres
    monkeypatch.setattr(tests_api, "settings", lambda: SimpleNamespace(adapter_mode="live"))
    with Session(engine) as db:
        check_id = baseline(db, account_id)
        case = tests_api.prepare(tests_api.TestCreate(account_id=account_id, check_id=check_id), db)
        case_id = case.id
        tests_api.approve(case_id, db)
    control = SimpleNamespace(state="none", follows_back=False, writes=[])

    class Adapter:
        def __init__(self, guard):
            self.guard = guard

        def relationship(self, target_id):
            self.guard.before_request()
            return RelationshipSnapshot(control.state, control.follows_back)

        def follow(self, target_id):
            self.guard.before_request(mutation=True, operation="follow", target_id=target_id)
            control.writes.append("follow")
            control.state = "following"
            if outcome == "unknown":
                raise UncertainWrite("Synthetic timeout after dispatch")
            return self.relationship(target_id)

        def unfollow(self, target_id):
            self.guard.before_request(mutation=True, operation="unfollow", target_id=target_id)
            control.writes.append("unfollow")
            control.state = "none"
            return self.relationship(target_id)

    if outcome == "protected":
        with Session(engine) as db, db.begin():
            db.scalar(
                select(Relationship).where(
                    Relationship.account_id == account_id, Relationship.target_id == "123"
                )
            ).protected = True
    executor = Executor(engine, Adapter, allow_mutations=outcome != "readonly")
    assert executor.run_account(account_id)
    if outcome == "followback":
        control.follows_back = True
    if outcome in {"restored", "followback"}:
        assert executor.run_account(account_id)
    with Session(engine) as db:
        case = db.get(RelationshipTest, case_id)
        relationship = db.scalar(
            select(Relationship).where(
                Relationship.account_id == account_id, Relationship.target_id == "123"
            )
        )
        assert case.state == (
            "restored"
            if outcome == "restored"
            else "unknown"
            if outcome == "unknown"
            else "review_required"
        )
        assert control.writes == (
            ["follow", "unfollow"]
            if outcome == "restored"
            else ["follow"]
            if outcome in {"followback", "unknown"}
            else []
        )
        if outcome in {"restored", "followback"}:
            assert relationship.ever_followed
            assert relationship.managed == (outcome == "followback")
        if outcome == "restored":
            assert relationship.observed_state == "none" and relationship.unfollow_confirmed_at
        if outcome == "unknown":
            assert case.unfollow_job_id is None
        account = db.get(Account, account_id)
        assert not account.live_acquisition_enabled and not account.live_cleanup_enabled
