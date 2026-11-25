import json
import uuid
from datetime import timedelta

import pytest
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from sqlalchemy import select

from app.models import Account, AccountConnection, OnboardingKey, now
from app.onboarding_crypto import key_id, public_key, seal, unseal


def publish(db):
    private = X25519PrivateKey.generate()
    public = public_key(private)
    db.add(OnboardingKey(id=key_id(public), public_key=public, observed_at=now()))
    db.commit()
    return private


def begin(client):
    return client.post(
        "/api/v1/account-connections",
        json={
            "username": "synthetic.owner",
            "password": "synthetic-private-password",
            "idempotency_key": str(uuid.uuid4()),
        },
    )


def test_secret_seal_binds_recipient_and_connection_scope():
    private = X25519PrivateKey.generate()
    envelope = seal(public_key(private), "synthetic-private-password", "connection1:password")
    assert "synthetic-private-password" not in json.dumps(envelope)
    assert unseal(private, envelope, "connection1:password") == "synthetic-private-password"
    with pytest.raises(InvalidTag):
        unseal(private, envelope, "connection2:password")
    with pytest.raises(InvalidTag):
        unseal(X25519PrivateKey.generate(), envelope, "connection1:password")


def test_browser_begin_seals_password_and_never_exposes_it(system):
    client, db, _, _ = system
    private = publish(db)
    response = begin(client)
    assert response.status_code == 202
    result = response.json()
    assert result["state"] == "queued"
    assert "password" not in response.text and "envelope" not in response.text
    connection = db.get(AccountConnection, result["id"])
    assert "synthetic-private-password" not in json.dumps(connection.password_envelope)
    assert (
        unseal(
            private,
            connection.password_envelope,
            f"{connection.id}:{connection.account_id}:password",
        )
        == "synthetic-private-password"
    )
    account = db.get(Account, connection.account_id)
    assert account.paused and account.mode == "live"
    assert not account.live_acquisition_enabled and not account.live_cleanup_enabled
    visible = client.get("/api/v1/accounts?mode=live").json()["items"]
    assert [item["id"] for item in visible] == [account.id]
    assert begin(client).status_code == 409


def test_code_submission_is_bound_to_challenge_and_one_use(system):
    client, db, _, _ = system
    private = publish(db)
    result = begin(client).json()
    connection = db.get(AccountConnection, result["id"])
    connection.state, connection.challenge_id = "awaiting_code", str(uuid.uuid4())
    db.commit()
    path = f"/api/v1/account-connections/{connection.id}/code"
    assert (
        client.post(path, json={"code": "654321", "challenge_id": "stale-challenge"}).status_code
        == 409
    )
    response = client.post(path, json={"code": "654321", "challenge_id": connection.challenge_id})
    assert response.status_code == 200 and response.json()["state"] == "verifying"
    assert "654321" not in response.text
    assert (
        unseal(private, connection.code_envelope, f"{connection.id}:{connection.account_id}:code")
        == "654321"
    )
    assert (
        client.post(
            path, json={"code": "654321", "challenge_id": connection.challenge_id}
        ).status_code
        == 409
    )
    response = client.post(f"/api/v1/account-connections/{connection.id}/cancel", json={})
    assert response.json()["state"] == "cancelled"
    assert connection.password_envelope is None and connection.code_envelope is None


def test_offline_worker_and_expiry_are_truthful(system):
    client, db, _, _ = system
    assert begin(client).status_code == 503
    publish(db)
    result = begin(client).json()
    connection = db.get(AccountConnection, result["id"])
    connection.expires_at = now() - timedelta(seconds=1)
    db.commit()
    response = client.get(f"/api/v1/accounts/{connection.account_id}/onboarding")
    assert response.json()["state"] == "expired"
    assert connection.password_envelope is None


def test_invalid_fields_and_auth_never_echo_credentials(system):
    client, db, _, _ = system
    publish(db)
    response = client.post(
        "/api/v1/account-connections",
        headers={"X-Internal-Token": "invalid-token"},
        json={"password": "synthetic-private-password"},
    )
    assert response.status_code == 401 and "synthetic-private-password" not in response.text
    response = client.post(
        "/api/v1/account-connections",
        json={
            "username": "invalid/username",
            "password": "synthetic-private-password",
            "idempotency_key": "12345678",
        },
    )
    assert response.status_code == 422 and "synthetic-private-password" not in response.text
    response = client.post(
        "/api/v1/account-connections",
        headers={"Origin": "https://unexpected.example"},
        json={
            "username": "synthetic.owner",
            "password": "synthetic-private-password",
            "idempotency_key": "12345678",
        },
    )
    assert response.status_code == 403
    assert db.scalar(select(AccountConnection)) is None


def test_phone_approval_is_explicit_challenge_bound_and_one_use(system):
    client, db, _, _ = system
    publish(db)
    result = begin(client).json()
    row = db.get(AccountConnection, result["id"])
    row.state, row.challenge_kind, row.challenge_id = (
        "awaiting_approval",
        "approval",
        str(uuid.uuid4()),
    )
    db.commit()
    path = f"/api/v1/account-connections/{row.id}/approval"
    assert (
        client.get(f"/api/v1/accounts/{row.account_id}/onboarding").json()["state"]
        == "awaiting_approval"
    )
    assert client.post(path, json={"challenge_id": "old-step"}).status_code == 409
    response = client.post(path, json={"challenge_id": row.challenge_id})
    assert response.status_code == 200 and response.json()["state"] == "verifying"
    assert not row.code_submitted
    assert client.post(path, json={"challenge_id": row.challenge_id}).status_code == 409


def test_code_prompt_choice_is_recorded_and_validated(system):
    client, db, _, _ = system
    publish(db)
    result = begin(client).json()
    row = db.get(AccountConnection, result["id"])
    row.state, row.challenge_kind, row.challenge_id = "awaiting_approval", "approval", "step-1"
    db.commit()
    path = f"/api/v1/account-connections/{row.id}/approval"
    assert client.post(path, json={"challenge_id": "step-1", "choice": "other"}).status_code == 422
    response = client.post(path, json={"challenge_id": "step-1", "choice": "code"})
    assert response.status_code == 200 and response.json()["state"] == "verifying"
    assert response.json()["challenge_kind"] == "approval_code"
    assert "code-entry screen" in response.json()["message"]
    log = client.get(f"/api/v1/account-connections/{row.id}/log").json()
    assert log["events"] == [] and log["request_count"] == 0
    assert client.get("/api/v1/account-connections/missing/log").status_code == 404


@pytest.mark.parametrize(
    "code", ["PROFILE_CONTINUATION_UNAVAILABLE", "PROFILE_VERIFICATION_REQUIRED"]
)
def test_unsupported_verification_does_not_claim_phone_failure_or_recommend_login_loop(
    system, code
):
    client, db, _, _ = system
    publish(db)
    result = begin(client).json()
    connection = db.get(AccountConnection, result["id"])
    connection.state, connection.error_code, connection.challenge_kind = "failed", code, "approval"
    connection.password_envelope = None
    db.commit()
    response = client.get(f"/api/v1/accounts/{connection.account_id}/onboarding").json()
    assert response["state"] == "failed" and response["error_code"] == code
    assert "does not" in response["message"]
    assert "reconnect" not in response["message"].lower()
    if code == "PROFILE_CONTINUATION_UNAVAILABLE":
        assert "after you acknowledged phone approval" in response["message"]
