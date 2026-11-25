from datetime import timedelta

from sqlalchemy import select

from app.models import Action, Job, now
from app.worker import tick


def payload(account, **changes):
    return {
        "account_id": account.id,
        "target_id": "demo-target",
        "kind": "follow",
        "idempotency_key": "test-intent-001",
        **changes,
    }


def test_auth_required(system):
    client, *_ = system
    assert client.get("/api/v1/accounts", headers={"X-Internal-Token": "wrong"}).status_code == 401


def test_origin_required(system):
    client, db, account, rel = system
    assert (
        client.post(
            "/api/v1/jobs", json=payload(account), headers={"Origin": "https://other.example"}
        ).status_code
        == 403
    )


def test_idempotent_enqueue(system):
    client, db, account, rel = system
    a = client.post("/api/v1/jobs", json=payload(account))
    b = client.post("/api/v1/jobs", json=payload(account))
    assert a.status_code == b.status_code == 202
    assert a.json()["id"] == b.json()["id"]
    assert len(list(db.scalars(select(Job)))) == 1


def test_key_reuse_conflict(system):
    client, db, account, rel = system
    client.post("/api/v1/jobs", json=payload(account))
    assert client.post("/api/v1/jobs", json=payload(account, kind="reconcile")).status_code == 409


def test_simulation_does_not_mutate_relationship(system):
    client, db, account, rel = system
    client.post("/api/v1/jobs", json=payload(account))
    assert tick(db)
    db.refresh(rel)
    assert rel.observed_state == "none" and not rel.ever_followed
    assert rel.follow_confirmed_at is None
    assert db.scalar(select(Action)).simulated
    assert db.scalar(select(Job)).state == "simulated"


def test_protection_rechecked_after_queue(system):
    client, db, account, rel = system
    rel.managed = True
    rel.observed_state = "following"
    db.commit()
    assert client.post("/api/v1/jobs", json=payload(account, kind="unfollow")).status_code == 202
    rel.protected = True
    db.commit()
    tick(db)
    job = db.scalar(select(Job))
    assert job.state == "skipped" and job.error == "protected"


def test_existing_unmanaged_unfollow_rejected(system):
    client, db, account, rel = system
    rel.observed_state = "following"
    db.commit()
    assert client.post("/api/v1/jobs", json=payload(account, kind="unfollow")).status_code == 409


def test_previous_follow_rejected(system):
    client, db, account, rel = system
    rel.ever_followed = True
    db.commit()
    assert client.post("/api/v1/jobs", json=payload(account)).status_code == 409


def test_pause_prevents_job_claim(system):
    client, db, account, rel = system
    client.post("/api/v1/jobs", json=payload(account))
    client.patch(f"/api/v1/accounts/{account.id}/pause", json={"paused": True})
    assert tick(db) is False
    assert db.scalar(select(Job)).state == "queued"


def test_version_conflict(system):
    client, db, account, rel = system
    path = f"/api/v1/relationships/{rel.id}"
    assert client.patch(path, json={"expected_version": 1, "protected": True}).status_code == 200
    assert client.patch(path, json={"expected_version": 1, "protected": False}).status_code == 409
    db.refresh(rel)
    assert rel.protected


def test_cancel_only_queued(system):
    client, db, account, rel = system
    job = client.post("/api/v1/jobs", json=payload(account)).json()
    path = f"/api/v1/jobs/{job['id']}/cancel"
    assert client.post(path).status_code == 200
    assert client.post(path).status_code == 409
    assert not tick(db)


def test_future_job_waits(system):
    client, db, account, rel = system
    future = (now() + timedelta(days=1)).isoformat()
    client.post("/api/v1/jobs", json=payload(account, scheduled_at=future))
    assert not tick(db)


def test_naive_schedule_rejected(system):
    client, db, account, rel = system
    assert (
        client.post(
            "/api/v1/jobs", json=payload(account, scheduled_at="2027-01-01T10:00:00")
        ).status_code
        == 422
    )


def test_unknown_fields_rejected(system):
    client, db, account, rel = system
    assert client.post("/api/v1/jobs", json=payload(account, force=True)).status_code == 422
