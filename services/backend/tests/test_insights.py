import os
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import insights
from app.adapters.instagram import LiveInstagramAdapter
from app.execution_errors import Inaccessible, TransientRead
from app.executor import Executor
from app.insight_schemas import InsightObservation, InsightProfile
from app.models import Account, AccountSettings, ActionAttempt, InsightCheck, Job, now
from app.schemas import SettingsValues


def test_api_queues_real_read_and_deduplicates_without_external_io(system, monkeypatch):
    client, db, account, _ = system
    monkeypatch.setattr(insights, "settings", lambda: SimpleNamespace(adapter_mode="read_only"))
    account.mode, account.session_state = "live", "connected"
    db.commit()
    body = {
        "account_id": account.id,
        "kind": "following",
        "target": "@Example",
        "requested_limit": 5,
        "idempotency_key": "insight-request-one",
    }
    response = client.post("/api/v1/insight-checks", json=body)
    assert response.status_code == 202
    item = response.json()
    assert item["target"] == "example" and item["state"] == "queued" and item["observed_at"] is None
    assert item["items"] == [] and item["requests_used"] == 0
    assert db.scalar(select(ActionAttempt)) is None
    assert client.post("/api/v1/insight-checks", json=body).json()["id"] == item["id"]
    assert (
        client.post("/api/v1/insight-checks", json={**body, "target": "someone.else"}).status_code
        == 409
    )
    assert client.get(f"/api/v1/insight-checks?account_id={account.id}").json()["total"] == 1
    assert client.get(f"/api/v1/insight-checks/{item['id']}").json()["job_id"] == item["job_id"]


def test_invalid_or_disconnected_requests_do_not_create_insight_jobs(system, monkeypatch):
    client, db, account, _ = system
    monkeypatch.setattr(insights, "settings", lambda: SimpleNamespace(adapter_mode="read_only"))
    account.mode = "live"
    db.commit()
    body = {
        "account_id": account.id,
        "kind": "likers",
        "target": "https://example.com/p/abc/",
        "idempotency_key": "invalid-insight-one",
    }
    assert client.post("/api/v1/insight-checks", json=body).status_code == 422
    assert (
        client.post(
            "/api/v1/insight-checks", json={**body, "target": "https://instagram.com/p/abc/"}
        ).status_code
        == 409
    )
    assert client.get("/api/v1/insight-checks?account_id=missing").status_code == 404
    assert db.scalar(select(InsightCheck)) is None and db.scalar(select(Job)) is None


def test_source_photo_import_requires_a_saved_photo_and_is_idempotent(system):
    client, db, account, _ = system
    account.mode, account.session_state = "live", "connected"
    before = Job(
        account_id=account.id, kind="insight", state="succeeded", idempotency_key="recent-posts"
    )
    db.add(before)
    db.flush()
    check = InsightCheck(
        account_id=account.id,
        job_id=before.id,
        kind="posts",
        target="example",
        observed_at=now(),
        items=[
            {"id": "71", "media_type": "Photo", "url": "https://www.instagram.com/p/ABC/"},
            {"id": "72", "media_type": "Carousel", "url": "https://www.instagram.com/p/DEF/"},
        ],
    )
    db.add(check)
    db.commit()
    path = f"/api/v1/insight-checks/{check.id}/import-photo"
    assert client.post(path, json={"item_id": "72"}).status_code == 409
    result = client.post(path, json={"item_id": "71"})
    assert result.status_code == 202
    assert result.json()["kind"] == "import_source_photo"
    assert client.post(path, json={"item_id": "71"}).json()["id"] == result.json()["id"]
    assert db.scalar(select(ActionAttempt)) is None


@pytest.mark.parametrize(
    "kind", ["profile", "relationship", "following", "followers", "posts", "stories", "likers"]
)
def test_reader_maps_bounded_values_and_never_returns_raw_private_data(kind):
    user = SimpleNamespace(
        pk="123",
        username="example",
        full_name="Example",
        follower_count=12,
        following_count=14,
        media_count=3,
        is_private=False,
        authorization="private-authorization",
        cookies={"sessionid": "private-cookie"},
    )
    media = SimpleNamespace(
        pk="456",
        code="AbcDef",
        media_type=1,
        caption_text="A caption",
        like_count=5,
        comment_count=2,
    )
    calls = []

    def private(endpoint, params=None):
        calls.append(endpoint)
        if endpoint.startswith("friendships/show/"):
            return {"following": False, "outgoing_request": False, "followed_by": False}
        return {"status": "ok", "users": [vars(user)], "next_max_id": "next"}

    client = SimpleNamespace(
        user_info_by_username_v1=lambda _: user,
        user_info_v1=lambda _: user,
        rank_token="private-rank-token",
        private_request=private,
        user_medias_paginated_v1=lambda *args, **kwargs: ([media], "next"),
        user_stories_v1=lambda *args, **kwargs: [media],
        media_pk_from_url=lambda _: "456",
        media_likers=lambda _: [user],
    )
    result = LiveInstagramAdapter(client).inspect(
        kind, "https://instagram.com/p/AbcDef/" if kind == "likers" else "example", 1
    )
    assert "private-" not in result.model_dump_json()
    assert len(result.items) <= 1
    if kind == "profile":
        assert result.profile.followers == 12
    elif kind == "relationship":
        assert result.relationship.state == "none" and result.relationship.follows_back is False
    else:
        assert result.items[0].id in {"123", "456"}
    if kind in {"likers", "stories", "posts"}:
        assert result.complete is False


def test_reader_rejects_a_repeated_post_cursor():
    client = SimpleNamespace(user_medias_paginated_v1=lambda *args, **kwargs: ([], "old"))
    with pytest.raises(TransientRead):
        LiveInstagramAdapter(client).inspect("posts", "example", 5, "old", "123")


@pytest.mark.skipif(
    not os.getenv("TEST_POSTGRES_URL"), reason="Requires isolated PostgreSQL account lane"
)
@pytest.mark.parametrize("accessible", [True, False])
def test_insight_worker_uses_guarded_lane_and_persists_only_valid_observations(
    postgres, accessible
):
    engine, account_id = postgres
    with Session(engine) as db, db.begin():
        account = db.get(Account, account_id)
        account.mode, account.session_state = "live", "connected"
        db.add(
            AccountSettings(
                account_id=account_id, values=SettingsValues(request_ceiling=20).model_dump()
            )
        )
        job = Job(
            account_id=account_id,
            kind="insight",
            idempotency_key="insight-worker-one",
            payload={"check_id": "check-one"},
        )
        db.add(job)
        db.flush()
        job_id = job.id
        db.add(
            InsightCheck(
                id="check-one",
                account_id=account_id,
                job_id=job.id,
                kind="profile",
                target="example",
                requested_limit=5,
            )
        )

    class Adapter:
        def __init__(self, guard):
            self.guard = guard

        def inspect(self, *args, **kwargs):
            self.guard.before_request()
            if not accessible:
                raise Inaccessible("Synthetic inaccessible profile")
            return InsightObservation(
                profile=InsightProfile(id="123", username="example", followers=12),
                complete=True,
                note="Reported profile counts",
            )

    assert Executor(engine, Adapter, allow_mutations=False).run_account(account_id)
    with Session(engine) as db:
        check, job = db.get(InsightCheck, "check-one"), db.get(Job, job_id)
        assert job.state == ("succeeded" if accessible else "failed")
        assert bool(check.observed_at) == accessible
        assert (check.profile or {}).get("followers") == (12 if accessible else None)
        assert (
            db.scalar(select(ActionAttempt.request_count).where(ActionAttempt.job_id == job_id))
            == 1
        )
