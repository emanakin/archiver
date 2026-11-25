import os
from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_foundation import payload

from app.dashboard import candidate_views
from app.models import (
    Account,
    Discovery,
    FollowIntent,
    InsightCheck,
    InsightWatch,
    Job,
    LikerObservation,
    Relationship,
    Source,
    Target,
    now,
)
from app.worker import tick


def source(client, account, value="@quiet.form"):
    response = client.post(
        "/api/v1/sources",
        json={
            "account_id": account.id,
            "kind": "profile_followers",
            "value": value,
            "label": "Quiet form",
            "requested_limit": 20,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.parametrize(
    "database",
    [
        "system",
        pytest.param(
            "postgres",
            marks=pytest.mark.skipif(not os.getenv("TEST_POSTGRES_URL"), reason="Needs PostgreSQL"),
        ),
    ],
)
def test_rank_counts_distinct_recent_evidence_before_pagination(request, database):
    fixture = request.getfixturevalue(database)
    engine, aid = (fixture[1].get_bind(), fixture[2].id) if database == "system" else fixture
    with Session(engine) as db:
        at = now()
        source = Source(
            account_id=aid, kind="profile_followers", value="seed", resolved_instagram_id="70"
        )
        post = Source(account_id=aid, kind="post_likers", value="https://www.instagram.com/p/ABC/")
        watch = InsightWatch(account_id=aid, target="https://www.instagram.com/reel/ABC/")
        check_job = Job(account_id=aid, kind="insight", idempotency_key="candidate-following")
        db.add_all([source, post, watch, check_job])
        for identity in ["1", "9", "70", "71"]:
            db.add(Target(id=identity, username=f"u{identity}"))
        db.flush()
        for identity in ["1", "9", "71"]:
            db.add(
                Relationship(
                    account_id=aid,
                    target_id=identity,
                    observed_state="following" if identity == "71" else "none",
                )
            )
        db.add_all(
            [
                Discovery(source_id=source.id, target_id="1"),
                Discovery(source_id=source.id, target_id="9"),
                Discovery(source_id=post.id, target_id="9"),
                LikerObservation(
                    watch_id=watch.id,
                    target_id="9",
                    username="u9",
                    first_seen_at=at,
                    last_seen_at=at,
                    baseline=True,
                ),
                InsightCheck(
                    account_id=aid,
                    job_id=check_job.id,
                    kind="following",
                    target="u9",
                    profile={"id": "9"},
                    items=[{"id": "70"}, {"id": "71"}, {"id": "72"}],
                    observed_at=at,
                ),
            ]
        )
        db.flush()
        (first,) = candidate_views(db, aid, source_id=source.id, limit=1)
        assert (first["target_id"], first["liked_posts"], first["shared_profiles"]) == ("9", 1, 2)
        (second,) = candidate_views(db, aid, limit=1, offset=1)
        assert (second["target_id"], second["liked_posts"], second["shared_profiles"]) == (
            "1",
            0,
            1,
        )


def test_account_order_stays_stable_after_mutation(system):
    client, db, account, _rel = system
    second = Account(
        username="second", mode="live", created_at=account.created_at + timedelta(seconds=1)
    )
    db.add(second)
    db.commit()
    client.patch(f"/api/v1/accounts/{account.id}/pause", json={"paused": True})
    assert [item["id"] for item in client.get("/api/v1/accounts").json()["items"]] == [
        account.id,
        second.id,
    ]


def test_queue_review_keeps_only_connection_attempts_without_a_later_success(system):
    client, db, account, _rel = system
    created = now()
    attempts = [
        Job(
            account_id=account.id,
            idempotency_key=key,
            kind=kind,
            state=state,
            created_at=created + timedelta(seconds=offset),
        )
        for offset, (key, kind, state) in enumerate(
            [
                ("old-connection", "connect", "blocked"),
                ("unknown-follow", "follow", "unknown"),
                ("connected", "connect", "succeeded"),
                ("latest-connection", "connect", "failed"),
                ("scheduled", "insight", "queued"),
            ]
        )
    ]
    db.add_all(attempts)
    db.commit()

    params = {"account_id": account.id}
    review = client.get("/api/v1/jobs", params={**params, "needs_review": True}).json()
    assert review["total"] == 2
    assert [item["id"] for item in review["items"]] == [attempts[3].id, attempts[1].id]
    history = client.get("/api/v1/jobs", params={**params, "state": "blocked"}).json()
    assert [item["id"] for item in history["items"]] == [attempts[0].id]


def test_definitely_unsent_follow_can_be_reviewed_again_without_reusing_receipts(system):
    client, db, account, rel = system
    saved_source = source(client, account)
    db.add(Discovery(source_id=saved_source["id"], target_id=rel.target_id))
    db.commit()
    first = client.post("/api/v1/jobs", json=payload(account)).json()
    assert (
        client.patch(
            f"/api/v1/relationships/{rel.id}",
            json={"expected_version": rel.version, "protected": True},
        ).status_code
        == 200
    )
    assert tick(db)
    assert db.get(Job, first["id"]).state == "skipped"
    # Exercise compatibility with a reservation left by an older release.
    if not db.scalar(select(FollowIntent).where(FollowIntent.job_id == first["id"])):
        db.add(FollowIntent(account_id=account.id, target_id=rel.target_id, job_id=first["id"]))
        db.commit()
    assert (
        client.patch(
            f"/api/v1/relationships/{rel.id}",
            json={"expected_version": rel.version, "protected": False},
        ).status_code
        == 200
    )
    candidates = client.get(f"/api/v1/candidates?account_id={account.id}&available=eligible").json()
    assert candidates["total"] == 1
    second = client.post(
        "/api/v1/jobs", json=payload(account, idempotency_key="review-after-unsent-skip")
    )
    assert second.status_code == 202 and second.json()["id"] != first["id"]
    assert client.post("/api/v1/jobs", json=payload(account)).json()["id"] == first["id"]
    assert not rel.ever_followed and rel.observed_state == "none"


def test_operating_metrics_distinguish_real_freshness_and_uncertain_writes(system):
    client, db, account, rel = system
    before = now() - timedelta(minutes=5)
    db.add(
        Job(
            account_id=account.id,
            target_id=rel.target_id,
            kind="follow",
            idempotency_key="old-queued-job",
            scheduled_at=before,
        )
    )
    db.add(
        Job(
            account_id=account.id,
            target_id=rel.target_id,
            kind="follow",
            idempotency_key="uncertain-job",
            state="unknown",
            dispatch_started_at=before,
            error="OUTCOME_UNKNOWN",
        )
    )
    db.commit()
    operations = client.get(f"/api/v1/overview?account_id={account.id}").json()["operations"]
    assert operations["oldest_due_age_seconds"] >= 300
    assert operations["unknown_writes"] == 1
    assert operations["last_relationship_observation"] is None
    assert operations["last_collection_at"] is None


def test_source_validation_and_canonical_duplicates(system):
    client, db, account, rel = system
    created = source(client, account, "@Quiet.Form")
    assert created["value"] == "quiet.form"
    assert (
        client.post(
            "/api/v1/sources",
            json={"account_id": account.id, "kind": "profile_followers", "value": "quiet.form"},
        ).status_code
        == 409
    )
    bad = client.post(
        "/api/v1/sources",
        json={
            "account_id": account.id,
            "kind": "post_likers",
            "value": "https://instagram.com.evil.example/p/abc/",
        },
    )
    assert bad.status_code == 422
    assert "input" not in str(bad.json()["error"]["details"])
    post = client.post(
        "/api/v1/sources",
        json={
            "account_id": account.id,
            "kind": "post_likers",
            "value": "http://instagram.com/reels/abc/?tracking=secret",
        },
    )
    assert post.json()["value"] == "https://www.instagram.com/reel/abc/"


def test_discovery_is_bounded_checkpointed_and_deduplicates_overlap(system):
    client, db, account, rel = system
    first, second = source(client, account), source(client, account, "second.form")
    for item in (first, second):
        path = f"/api/v1/sources/{item['id']}/collect"
        accepted = client.post(path, json={"idempotency_key": "collect-" + item["id"]})
        assert accepted.status_code == 202
        assert (
            client.post(path, json={"idempotency_key": "collect-" + item["id"]}).json()["id"]
            == accepted.json()["id"]
        )
        # Two ten-item pages, one persisted continuation.
        assert tick(db)
        assert tick(db)
    candidates = client.get(f"/api/v1/candidates?account_id={account.id}&limit=100").json()
    assert candidates["total"] == 35
    shared = [x for x in candidates["items"] if len(x["source_ids"]) == 2]
    assert len(shared) == 5
    assert all(x["eligible"] for x in candidates["items"])
    assert db.scalar(select(func.count()).select_from(Discovery)) == 40
    sources = client.get(f"/api/v1/sources?account_id={account.id}").json()["items"]
    assert all(
        x["collection_state"] == "partial"
        and x["collection_cursor"] == "20"
        and x["overlap_count"] == 5
        for x in sources
    )
    assert not any(
        x.ever_followed or x.follow_confirmed_at or x.last_checked_at
        for x in db.scalars(select(Relationship))
    )


def test_canonical_schedule_conflict_and_business_dedup(system):
    client, db, account, rel = system
    when = (now() + timedelta(days=1)).isoformat()
    first = client.post("/api/v1/jobs", json=payload(account, scheduled_at=when)).json()
    conflict = client.post(
        "/api/v1/jobs", json=payload(account, scheduled_at=(now() + timedelta(days=2)).isoformat())
    )
    assert conflict.status_code == 409
    duplicate = client.post(
        "/api/v1/jobs", json=payload(account, idempotency_key="another-request-key")
    )
    assert duplicate.json()["id"] == first["id"]
    assert db.scalar(select(func.count()).select_from(FollowIntent)) == 1
    assert client.post(f"/api/v1/jobs/{first['id']}/cancel").status_code == 200
    assert (
        client.post(
            "/api/v1/jobs", json=payload(account, idempotency_key="after-cancellation")
        ).json()["id"]
        != first["id"]
    )


def test_settings_versions_and_account_boundaries(system):
    client, db, account, rel = system
    path = f"/api/v1/accounts/{account.id}/settings"
    original = client.get(path).json()
    assert original["request_ceiling"] == 0
    values = {k: v for k, v in original.items() if k != "version"}
    saved = client.patch(path, json={**values, "retention_days": 21, "expected_version": 1})
    assert saved.status_code == 200 and saved.json()["version"] == 2
    stale = client.patch(path, json={**values, "expected_version": 1})
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "VERSION_CONFLICT"
    for name in ("sources", "relationships", "jobs", "candidates", "worker-status"):
        assert client.get(f"/api/v1/{name}?account_id=missing").status_code == 404
    assert (
        client.patch(
            path, json={**values, "timezone": "Bogus/Zone", "expected_version": 2}
        ).status_code
        == 422
    )


def test_bulk_rejections_and_simulation_history_isolation(system):
    client, db, account, rel = system
    db.add(Target(id="demo-protected", username="protected"))
    db.flush()
    db.add(
        Relationship(
            account_id=account.id, target_id="demo-protected", protected=True, observed_state="none"
        )
    )
    db.commit()
    response = client.post(
        "/api/v1/jobs/bulk",
        json={
            "account_id": account.id,
            "target_ids": [rel.target_id, "demo-protected", "missing"],
            "idempotency_key": "reviewed-batch",
        },
    )
    assert response.status_code == 202, response.text
    assert len(response.json()["jobs"]) == 1 and len(response.json()["rejected"]) == 2
    assert tick(db)
    db.refresh(rel)
    assert rel.observed_state == "none" and not rel.ever_followed
    overview = client.get(f"/api/v1/overview?account_id={account.id}").json()
    assert (
        overview["follows_used"] == 0
        and overview["live_metrics"] is None
        and overview["chart"] == []
    )
    assert client.get(f"/api/v1/audit-events?account_id={account.id}").json()["total"] >= 2


def test_disabled_source_skips_queued_collection(system):
    client, db, account, rel = system
    created = source(client, account)
    client.post(
        f"/api/v1/sources/{created['id']}/collect", json={"idempotency_key": "collect-disabled"}
    )
    client.patch(f"/api/v1/sources/{created['id']}", json={"enabled": False})
    assert tick(db)
    assert db.scalar(select(Job)).state == "skipped"
    assert db.scalar(select(func.count()).select_from(Discovery)) == 0


def test_alias_request_key_is_not_reusable_for_another_target(system):
    client, db, account, rel = system
    first = client.post("/api/v1/jobs", json=payload(account)).json()
    alias = client.post("/api/v1/jobs", json=payload(account, idempotency_key="business-alias-key"))
    assert alias.json()["id"] == first["id"]
    db.add(Target(id="other-target", username="other_sample"))
    db.flush()
    db.add(Relationship(account_id=account.id, target_id="other-target", observed_state="none"))
    db.commit()
    changed = client.post(
        "/api/v1/jobs",
        json=payload(account, target_id="other-target", idempotency_key="business-alias-key"),
    )
    assert changed.status_code == 409


def test_bulk_replays_original_acceptance_and_rejects_changed_selection(system):
    client, db, account, rel = system
    body = {
        "account_id": account.id,
        "target_ids": [rel.target_id],
        "idempotency_key": "review-bulk-receipt",
    }
    first = client.post("/api/v1/jobs/bulk", json=body)
    assert first.status_code == 202
    rel.protected = True
    db.commit()
    replay = client.post("/api/v1/jobs/bulk", json=body)
    assert replay.json() == first.json()
    assert (
        client.post("/api/v1/jobs/bulk", json={**body, "target_ids": ["different"]}).status_code
        == 409
    )


def test_candidate_filters_run_before_pagination(system):
    client, db, account, rel = system
    created = source(client, account)
    client.post(
        f"/api/v1/sources/{created['id']}/collect", json={"idempotency_key": "filtered-discovery"}
    )
    assert tick(db) and tick(db)
    base = f"/api/v1/candidates?account_id={account.id}&source_id={created['id']}"
    first = client.get(base + "&limit=8").json()
    second = client.get(base + "&limit=8&offset=8").json()
    assert first["total"] == second["total"] == 20
    assert not set(x["target_id"] for x in first["items"]) & set(
        x["target_id"] for x in second["items"]
    )
    name = second["items"][-1]["username"]
    assert client.get(base + f"&q={name}&limit=8").json()["items"][0]["username"] == name
    record = db.scalar(
        select(Relationship).where(Relationship.target_id == first["items"][0]["target_id"])
    )
    record.protected = True
    db.commit()
    assert client.get(base + "&available=eligible").json()["total"] == 19
    assert client.get(base + "&available=ineligible").json()["total"] == 1
    record.excluded = True
    db.commit()
    assert client.get(base + "&available=active").json()["total"] == 19
    excluded = client.get(base + "&category=excluded").json()
    assert excluded["total"] == 1
    assert excluded["items"][0]["target_id"] == record.target_id


def test_recovery_endpoints_never_retry_a_write(system):
    client, db, account, rel = system
    job = client.post("/api/v1/jobs", json=payload(account)).json()
    assert (
        client.post(
            f"/api/v1/jobs/{job['id']}/retry", json={"idempotency_key": "no-follow-retry"}
        ).status_code
        == 409
    )
    assert (
        client.post(
            f"/api/v1/jobs/{job['id']}/reconcile", json={"idempotency_key": "not-dispatched"}
        ).status_code
        == 409
    )
    original = db.get(Job, job["id"])
    original.state, original.dispatch_started_at = "unknown", now()
    db.commit()
    body = {"idempotency_key": "read-unknown-outcome"}
    check = client.post(f"/api/v1/jobs/{job['id']}/reconcile", json=body)
    assert check.status_code == 202
    assert (
        check.json()["kind"] == "reconcile"
        and check.json()["payload"]["reconcile_job_id"] == job["id"]
    )
    assert (
        client.post(f"/api/v1/jobs/{job['id']}/reconcile", json=body).json()["id"]
        == check.json()["id"]
    )
    assert db.get(Job, job["id"]).state == "unknown"
    db.get(Job, check.json()["id"]).state = "failed"
    db.commit()
    retry = client.post(
        f"/api/v1/jobs/{check.json()['id']}/retry", json={"idempotency_key": "retry-read-only"}
    )
    assert retry.status_code == 202 and retry.json()["kind"] == "reconcile"
    assert retry.json()["payload"]["retry_depth"] == 1
    assert client.get(f"/api/v1/jobs/{job['id']}/attempts").json()["items"] == []
