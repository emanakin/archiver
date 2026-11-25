import os
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.instagram import RelationshipSnapshot
from app.cleanup import materialize_cleanup
from app.executor import Executor
from app.models import Account, AccountSettings, Job, Relationship, now
from app.schemas import SettingsValues

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_POSTGRES_URL"), reason="Requires isolated PostgreSQL schema"
)


def prepare(postgres):
    engine, account_id = postgres
    with Session(engine) as db:
        account = db.get(Account, account_id)
        account.mode, account.session_state = "live", "connected"
        account.live_cleanup_enabled, account.read_only_verified_at = True, now()
        db.add(
            AccountSettings(
                account_id=account_id,
                values=SettingsValues(unfollows_per_day=5, request_ceiling=50).model_dump(),
            )
        )
        rel = db.scalar(select(Relationship))
        rel.managed, rel.ever_followed, rel.observed_state = True, True, "following"
        rel.follow_confirmed_at = now() - timedelta(days=20)
        db.commit()
    return engine, account_id


class Remote:
    def __init__(self, follows_back=False):
        self.state, self.writes, self.follows_back = "following", 0, follows_back
        self.before_write = None

    def adapter(self, guard):
        remote = self

        class Adapter:
            def relationship(self, _target):
                guard.before_request()
                return RelationshipSnapshot(remote.state, remote.follows_back)

            def unfollow(self, target):
                if remote.before_write:
                    remote.before_write()
                guard.before_request(mutation=True)
                remote.writes += 1
                remote.state = "none"
                return self.relationship(target)

        return Adapter()


def test_due_cleanup_is_deduplicated_and_preserves_history(postgres):
    engine, account_id = prepare(postgres)
    assert materialize_cleanup(engine) == 1
    assert materialize_cleanup(engine) == 0
    remote = Remote()
    assert Executor(engine, remote.adapter).run_once()
    assert remote.writes == 1
    assert materialize_cleanup(engine) == 0
    with Session(engine) as db:
        rel = db.scalar(select(Relationship))
        assert rel.observed_state == "none" and rel.ever_followed
        assert rel.unfollow_confirmed_at is not None and rel.follow_confirmed_at is not None


@pytest.mark.parametrize("follows_back", [True, None])
def test_reciprocal_or_unknown_follow_back_is_preserved(postgres, follows_back):
    engine, account_id = prepare(postgres)
    assert materialize_cleanup(engine) == 1
    remote = Remote(follows_back)
    assert Executor(engine, remote.adapter).run_once()
    assert remote.writes == 0
    with Session(engine) as db:
        assert db.scalar(select(Job)).state == "skipped"
        assert db.scalar(select(Relationship)).observed_state == "following"
    assert materialize_cleanup(engine) == 0  # No repeated read/write loop today.


def test_protection_committed_after_preflight_prevents_cleanup(postgres):
    engine, account_id = prepare(postgres)
    materialize_cleanup(engine)
    remote = Remote()

    def protect():
        with Session(engine) as db:
            db.scalar(select(Relationship)).protected = True
            db.commit()

    remote.before_write = protect
    assert Executor(engine, remote.adapter).run_once()
    assert remote.writes == 0
    with Session(engine) as db:
        assert db.scalar(select(Job)).error == "protected"


def test_new_follow_back_observation_before_dispatch_preserves_connection(postgres):
    engine, account_id = prepare(postgres)
    materialize_cleanup(engine)
    remote = Remote()

    def observe_follow_back():
        with Session(engine) as db:
            rel = db.scalar(select(Relationship))
            rel.follows_back, rel.follows_back_observed_at = True, now()
            db.commit()

    remote.before_write = observe_follow_back
    assert Executor(engine, remote.adapter).run_once()
    assert remote.writes == 0
    with Session(engine) as db:
        job = db.scalar(select(Job))
        assert job.state == "skipped" and "nonreciprocal" in job.error


def test_retention_changes_reschedule_existing_cleanup(postgres):
    engine, account_id = prepare(postgres)
    materialize_cleanup(engine)
    with Session(engine) as db:
        config = db.get(AccountSettings, account_id)
        config.values = {**config.values, "retention_days": 30}
        db.commit()
    assert materialize_cleanup(engine) == 0
    remote = Remote()
    assert not Executor(engine, remote.adapter).run_once()
    with Session(engine) as db:
        assert db.scalar(select(Job)).scheduled_at > now() + timedelta(days=9)
    assert remote.writes == 0


@pytest.mark.parametrize("flag", ["protected", "excluded", "unmanaged", "unknown_age"])
def test_ineligible_relationships_never_materialize_cleanup(postgres, flag):
    engine, account_id = prepare(postgres)
    with Session(engine) as db:
        rel = db.scalar(select(Relationship))
        if flag == "unmanaged":
            rel.managed = False
        elif flag == "unknown_age":
            rel.follow_confirmed_at = None
        else:
            setattr(rel, flag, True)
        db.commit()
    assert materialize_cleanup(engine) == 0
