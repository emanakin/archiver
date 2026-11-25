import uuid
from datetime import timedelta
from types import SimpleNamespace

from app.models import AccountSession, AccountSettings, now
from app.schemas import SettingsValues


def test_workflow_enable_requires_proof_budget_ack_and_version(system, monkeypatch):
    client, db, account, _rel = system
    account.mode, account.session_state, account.paused = "live", "connected", True
    account.read_only_verified_at = now()
    db.add(
        AccountSettings(
            account_id=account.id,
            values=SettingsValues(
                follows_per_day=2, unfollows_per_day=2, request_ceiling=20
            ).model_dump(),
        )
    )
    metadata = AccountSession(
        account_id=account.id, revision=str(uuid.uuid4()), library_version="3.2.15", capabilities={}
    )
    db.add(metadata)
    db.commit()
    path = f"/api/v1/accounts/{account.id}/workflows"
    body = {"workflow": "acquisition", "enabled": True, "expected_version": 1, "acknowledged": True}
    assert (
        client.patch(path, json=body).status_code == 409
    )  # Mock runtime never enables live actions.
    monkeypatch.setattr(
        "app.workflow_routes.settings", lambda: SimpleNamespace(adapter_mode="live")
    )
    assert client.patch(path, json={**body, "acknowledged": False}).status_code == 422
    assert client.patch(path, json=body).status_code == 409  # No collection capability proof.
    metadata.capabilities = {"bounded_collection_verified": True}
    db.commit()
    enabled = client.patch(path, json=body)
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()["live_acquisition_enabled"] and enabled.json()["paused"]
    assert enabled.json()["workflow_version"] == 2
    assert client.patch(path, json=body).status_code == 409
    disabled = client.patch(
        path, json={**body, "enabled": False, "expected_version": 2, "acknowledged": False}
    )
    assert disabled.status_code == 200 and not disabled.json()["live_acquisition_enabled"]


def test_adoption_requires_real_account_and_starts_retention_now(system):
    client, db, account, rel = system
    rel.observed_state, rel.follow_confirmed_at = "following", now() - timedelta(days=90)
    db.commit()
    path = f"/api/v1/relationships/{rel.id}/adopt"
    body = {"expected_version": 1, "acknowledged": True}
    assert client.post(path, json=body).status_code == 409
    account.mode = "live"
    db.commit()
    assert client.post(path, json={**body, "acknowledged": False}).status_code == 422
    adopted = client.post(path, json=body)
    assert adopted.status_code == 200, adopted.text
    db.refresh(rel)
    assert rel.managed and rel.ever_followed and rel.version == 2
    assert rel.adopted_at is not None
    assert timedelta(days=14) < rel.unfollow_due_at - rel.adopted_at < timedelta(days=16)
    assert client.post(path, json=body).status_code == 409
