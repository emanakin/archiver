import os
from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.adapters.instagram import (
    CollectionPage,
    DiscoveredUser,
    LiveInstagramAdapter,
    RelationshipSnapshot,
)
from app.dashboard import account_settings
from app.execution_errors import Inaccessible, Restriction, TransientRead
from app.executor import Executor
from app.models import (
    Account,
    AccountSettings,
    Discovery,
    Job,
    Relationship,
    Source,
    UsernameHistory,
    now,
)
from app.schemas import SettingsValues
from app.timing import local_day

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_POSTGRES_URL"), reason="Requires isolated PostgreSQL schema"
)


def prepare(postgres):
    engine, account_id = postgres
    with Session(engine) as db:
        account = db.get(Account, account_id)
        account.mode, account.session_state, account.instagram_id = "live", "connected", "999"
        db.add(
            AccountSettings(
                account_id=account_id, values=SettingsValues(request_ceiling=100).model_dump()
            )
        )
        source = Source(
            account_id=account_id,
            kind="profile_followers",
            value="sample_source",
            requested_limit=20,
        )
        db.add(source)
        db.flush()
        db.add(
            Job(
                account_id=account_id,
                kind="discover",
                idempotency_key="live-source-page",
                payload={"source_id": source.id, "remaining": 20},
            )
        )
        source_id = source.id
        db.commit()
    return engine, account_id, source_id


def test_live_pages_checkpoint_deduplicate_and_observe_without_writing(postgres):
    engine, account_id, source_id = prepare(postgres)

    class Adapter:
        def __init__(self, guard):
            self.guard = guard

        def resolve_user(self, username):
            self.guard.before_request()
            return DiscoveredUser("100", username)

        def collect_followers(self, user_id, cursor, amount, order):
            self.guard.before_request()
            start = 0 if cursor is None else 5
            users = tuple(
                DiscoveredUser(str(10000 + index), f"sample_{index}")
                for index in range(start, start + 10)
            )
            return CollectionPage(
                users, "next-page" if cursor is None else None, cursor is not None
            )

        def relationship(self, target_id):
            self.guard.before_request()
            return RelationshipSnapshot("none", False)

    executor = Executor(engine, Adapter, allow_mutations=False)
    assert executor.run_once() and executor.run_once()
    with Session(engine) as db:
        source = db.get(Source, source_id)
        assert source.collection_complete and source.collection_state == "completed"
        assert source.resolved_instagram_id == "100"
        assert db.scalar(select(func.count()).select_from(Discovery)) == 15
        assert db.scalar(select(func.count()).select_from(UsernameHistory)) == 15
        rels = db.scalars(
            select(Relationship).where(Relationship.target_id != "demo-concurrent")
        ).all()
        assert all(
            x.observed_state == "unknown" and not x.ever_followed and not x.managed for x in rels
        )
    assert executor.run_once()  # One queued relationship observation.
    with Session(engine) as db:
        rels = db.scalars(
            select(Relationship).where(Relationship.target_id != "demo-concurrent")
        ).all()
        assert sum(x.observed_state == "none" for x in rels) == 1
        assert all(not x.ever_followed for x in rels)


def test_liker_collection_retains_full_configured_sample_in_one_read(postgres):
    engine, account_id, source_id = prepare(postgres)
    with Session(engine) as db, db.begin():
        source = db.get(Source, source_id)
        source.kind, source.value = "post_likers", "https://www.instagram.com/p/example/"

    class Adapter:
        def __init__(self, guard):
            self.guard = guard

        def collect_likers(self, url, amount):
            self.guard.before_request()
            assert amount == 20
            return CollectionPage(
                tuple(DiscoveredUser(str(10000 + i), f"liker_{i}") for i in range(amount)),
                None,
                False,
            )

    assert Executor(engine, Adapter, allow_mutations=False).run_once()
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(Discovery)) == 20
        assert db.scalar(select(func.count()).where(Job.kind == "discover")) == 1
        assert db.scalar(select(func.count()).where(Job.kind == "reconcile")) == 20
        assert db.get(Source, source_id).collection_state == "partial"


@pytest.mark.parametrize(
    ("failure", "expected_state"),
    [(TransientRead, "queued"), (Restriction, "blocked")],
)
def test_recurring_collection_backs_off_only_for_transient_reads(postgres, failure, expected_state):
    engine, account_id, source_id = prepare(postgres)
    at = now().replace(hour=16, minute=0, second=0, microsecond=0)
    with Session(engine) as db, db.begin():
        source = db.get(Source, source_id)
        source.schedule_enabled = True
        job = db.scalar(select(Job).where(Job.kind == "discover"))
        job.scheduled_at = at
        job.payload = {
            **job.payload,
            "recurring": True,
            "run_local_day": local_day(at, account_settings(db, account_id)),
        }

    class Adapter:
        def __init__(self, guard):
            self.guard = guard

        def resolve_user(self, username):
            self.guard.before_request()
            raise failure("Synthetic collection failure")

    for delay in [5, 10, 20, None] if expected_state == "queued" else [None]:
        assert Executor(engine, Adapter, clock=lambda at=at: at, allow_mutations=False).run_once()
        with Session(engine) as db:
            job = db.scalar(select(Job).where(Job.kind == "discover"))
            if delay is None:
                assert job.state == ("blocked" if failure is Restriction else "failed")
            else:
                assert job.state == "queued"
                assert job.scheduled_at == at + timedelta(minutes=delay)
            assert db.get(Account, account_id).restricted == (failure is Restriction)
        if delay is not None:
            at += timedelta(minutes=delay)


def test_inaccessible_collection_does_not_become_empty_success(postgres):
    engine, account_id, source_id = prepare(postgres)

    class Adapter:
        def __init__(self, guard):
            self.guard = guard

        def resolve_user(self, username):
            self.guard.before_request()
            raise Inaccessible("Synthetic inaccessible source")

    assert Executor(engine, Adapter, allow_mutations=False).run_once()
    with Session(engine) as db:
        source = db.get(Source, source_id)
        assert source.collection_state == "error" and not source.collection_complete
        assert source.collection_note == "SOURCE_INACCESSIBLE"
        assert db.scalar(select(func.count()).select_from(Discovery)) == 0


def test_mutual_collection_requires_both_positive_ids_and_queues_no_follows(postgres):
    engine, account_id, source_id = prepare(postgres)
    with Session(engine) as db, db.begin():
        source = db.get(Source, source_id)
        source.kind, source.requested_limit = "profile_mutuals", 3
        job = db.scalar(select(Job).where(Job.kind == "discover"))
        job.payload = {**job.payload, "remaining": 3}
    calls = []

    class Adapter(LiveInstagramAdapter):
        def __init__(self, guard):
            super().__init__(self)
            self.guard = guard

        def resolve_user(self, username):
            self.guard.before_request()
            return DiscoveredUser("100", username)

        def private_request(self, endpoint, params):
            self.guard.before_request()
            calls.append((endpoint, params.get("query")))
            if endpoint.endswith("/following/"):
                return {
                    "status": "ok",
                    "users": [
                        {"pk": "101", "username": "matched"},
                        {"pk": "102", "username": "same_name"},
                        {"pk": "999", "username": "self"},
                    ],
                    "next_max_id": "next",
                }
            ids = {"matched": "101", "same_name": "105", "self": "999"}
            match = params["query"]
            return {"status": "ok", "users": [{"pk": ids[match], "username": match}]}

        rank_token = "fixture"

    assert Executor(engine, Adapter, allow_mutations=False).run_once()
    assert calls == [
        ("friendships/100/following/", None),
        ("friendships/100/followers/", "matched"),
        ("friendships/100/followers/", "same_name"),
        ("friendships/100/followers/", "self"),
    ]
    with Session(engine) as db:
        source = db.get(Source, source_id)
        assert source.collection_cursor == "next" and source.collection_state == "partial"
        assert db.scalars(select(Discovery.target_id)).all() == ["101"]
        assert db.scalars(select(Job.kind).where(Job.state == "queued")).all() == ["reconcile"]


def test_existing_following_import_never_adopts_relationships(postgres):
    engine, account_id = postgres
    with Session(engine) as db:
        account = db.get(Account, account_id)
        account.mode, account.session_state, account.instagram_id = "live", "connected", "999"
        db.add(
            AccountSettings(
                account_id=account_id, values=SettingsValues(request_ceiling=10).model_dump()
            )
        )
        db.add(
            Job(
                account_id=account_id,
                kind="import_following",
                idempotency_key="import-existing",
                payload={"remaining": 10},
            )
        )
        db.commit()

    class Adapter:
        def __init__(self, guard):
            self.guard = guard

        def collect_following(self, user_id, cursor, amount):
            self.guard.before_request()
            return CollectionPage((DiscoveredUser("123", "existing_sample"),), None, True)

    assert Executor(engine, Adapter, allow_mutations=False).run_once()
    with Session(engine) as db:
        rel = db.scalar(select(Relationship).where(Relationship.target_id == "123"))
        assert rel.ever_followed and rel.observed_state == "following" and not rel.managed
        assert rel.unfollow_due_at is None
