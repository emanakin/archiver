import os
import threading
import time
import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import manage
from app.db import get_db
from app.execution_errors import SessionRequired
from app.main import app
from app.models import Account, AccountConnection, ActionAttempt, BudgetUsage, Job, now
from app.onboarding_worker import AccountOnboardingWorker

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_POSTGRES_URL"), reason="Requires isolated PostgreSQL account lane"
)


@pytest.fixture
def browser_system(postgres, monkeypatch, tmp_path):
    engine, _ = postgres
    key = tmp_path / "key"
    key.write_bytes(Fernet.generate_key())
    config = SimpleNamespace(
        adapter_mode="read_only", session_key_file=key, session_store_path=tmp_path / "sessions"
    )
    monkeypatch.setattr(manage, "engine", engine)
    monkeypatch.setattr(manage, "settings", lambda: config)
    monkeypatch.setattr(manage, "verify_installation", lambda: {"version": "3.2.15"})
    from app import onboarding_routes

    monkeypatch.setattr(onboarding_routes, "settings", lambda: config)
    control = SimpleNamespace(calls=[], approval=False)

    def build(guard, values):
        class Client:
            password = None

            def login(self, username, password, verification_code):
                assert password == "synthetic-private-password" and not verification_code
                guard.before_request()
                control.calls.append("password")
                if control.approval:
                    guard.record_event(
                        "verification_screens",
                        {
                            "stage": "entrypoint",
                            "screens": ["com.bloks.www.synthetic.approval"],
                            "session": False,
                            "context_data": "synthetic-private-context",
                        },
                    )
                    control.choice = guard.profile_approval_provider()
                    guard.record_event("owner_choice", {"choice": control.choice})
                    guard.before_request()
                    control.calls.append("approval-check")
                code = guard.mfa_code_provider()
                guard.before_request()
                control.calls.append("code")
                if code != "654321":
                    raise SessionRequired("Synthetic invalid code")

            def account_info(self):
                guard.before_request()
                control.calls.append("identity")
                return SimpleNamespace(pk="12345", username="synthetic.owner")

            def get_settings(self):
                return {"device_id": "synthetic-device"}

        return Client()

    monkeypatch.setattr(manage, "build_client", build)

    def database():
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_db] = database
    stop = threading.Event()
    worker = AccountOnboardingWorker(engine, stop, key_path=key)
    worker.publish_key()
    with TestClient(
        app,
        headers={
            "X-Internal-Token": os.environ["INTERNAL_API_TOKEN"],
            "Origin": "http://localhost:8080",
        },
    ) as client:
        yield client, engine, worker, stop, control
    stop.set()
    app.dependency_overrides.clear()


@pytest.mark.parametrize("completion", ["success", "wrong_code", "cancel", "expire"])
def test_browser_password_then_code_with_no_cli_or_plaintext_storage(browser_system, completion):
    client, engine, worker, stop, control = browser_system
    result = client.post(
        "/api/v1/account-connections",
        json={
            "username": "synthetic.owner",
            "password": "synthetic-private-password",
            "idempotency_key": str(uuid.uuid4()),
        },
    ).json()
    thread = threading.Thread(target=worker.run_once)
    thread.start()
    try:
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            state = client.get(f"/api/v1/accounts/{result['account_id']}/onboarding").json()
            if state["state"] == "awaiting_code":
                break
            time.sleep(0.02)
        assert state["state"] == "awaiting_code" and control.calls == ["password"]
        with Session(engine) as db:
            assert db.get(AccountConnection, result["id"]).password_envelope is None
        # A reload can recover exactly the same waiting challenge.
        assert (
            client.get(f"/api/v1/accounts/{result['account_id']}/onboarding").json()["challenge_id"]
            == state["challenge_id"]
        )
        if completion in {"success", "wrong_code"}:
            response = client.post(
                f"/api/v1/account-connections/{result['id']}/code",
                json={
                    "code": "654321" if completion == "success" else "123456",
                    "challenge_id": state["challenge_id"],
                },
            )
            assert response.status_code == 200
        elif completion == "cancel":
            assert (
                client.post(f"/api/v1/account-connections/{result['id']}/cancel", json={}).json()[
                    "state"
                ]
                == "cancelled"
            )
        else:
            with Session(engine) as db, db.begin():
                db.get(AccountConnection, result["id"]).expires_at = now() - timedelta(seconds=1)
            assert (
                client.get(f"/api/v1/accounts/{result['account_id']}/onboarding").json()["state"]
                == "expired"
            )
        thread.join(8)
        assert not thread.is_alive()
        state = client.get(f"/api/v1/accounts/{result['account_id']}/onboarding").json()
        assert (
            state["state"]
            == {
                "success": "connected",
                "wrong_code": "failed",
                "cancel": "cancelled",
                "expire": "expired",
            }[completion]
        )
        with Session(engine) as db:
            connection = db.get(AccountConnection, result["id"])
            assert connection.password_envelope is None and connection.code_envelope is None
            account = db.get(Account, result["account_id"])
            assert (
                account.paused
                and not account.live_acquisition_enabled
                and not account.live_cleanup_enabled
            )
            assert (account.session_state == "connected") == (completion == "success")
            assert "synthetic-private-password" not in str(db.scalar(select(Job)).payload)
            assert db.scalar(select(BudgetUsage)).reserved == 0
            assert (
                db.scalar(select(ActionAttempt)).request_count
                == {"success": 3, "wrong_code": 2, "cancel": 1, "expire": 1}[completion]
            )
    finally:
        stop.set()
        thread.join(5)


def test_worker_restart_erases_stale_handoff_without_replaying_password(browser_system):
    client, engine, worker, stop, control = browser_system
    result = client.post(
        "/api/v1/account-connections",
        json={
            "username": "synthetic.owner",
            "password": "synthetic-private-password",
            "idempotency_key": str(uuid.uuid4()),
        },
    ).json()
    with Session(engine) as db, db.begin():
        row = db.get(AccountConnection, result["id"])
        row.state = "awaiting_code"
        row.owner = str(uuid.uuid4())
        row.heartbeat_at = now() - timedelta(seconds=91)
        row.code_envelope = {"synthetic": "encrypted-only"}
    worker.run_once()
    state = client.get(f"/api/v1/accounts/{result['account_id']}/onboarding").json()
    assert state["state"] == "failed" and state["error_code"] == "WORKER_RESTARTED"
    assert control.calls == []
    with Session(engine) as db:
        row = db.get(AccountConnection, result["id"])
        assert row.password_envelope is None and row.code_envelope is None


@pytest.mark.parametrize("completion", ["continue", "continue_code", "cancel", "expire"])
def test_phone_approval_keeps_same_worker_then_allows_mfa(browser_system, completion):
    client, engine, worker, stop, control = browser_system
    control.approval = True
    result = client.post(
        "/api/v1/account-connections",
        json={
            "username": "synthetic.owner",
            "password": "synthetic-private-password",
            "idempotency_key": str(uuid.uuid4()),
        },
    ).json()
    thread = threading.Thread(target=worker.run_once)
    thread.start()

    def wait_state(expected):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            value = client.get(f"/api/v1/accounts/{result['account_id']}/onboarding").json()
            if value["state"] == expected:
                return value
            time.sleep(0.02)
        pytest.fail(f"Worker did not reach {expected}")

    try:
        state = wait_state("awaiting_approval")
        with Session(engine) as db:
            before_heartbeat = db.scalar(select(Job)).heartbeat_at
        time.sleep(0.7)
        assert control.calls == ["password"] and thread.is_alive()
        with Session(engine) as db:
            assert db.scalar(select(Job)).heartbeat_at > before_heartbeat
        if completion.startswith("continue"):
            choice = "code" if completion == "continue_code" else "approved"
            response = client.post(
                f"/api/v1/account-connections/{result['id']}/approval",
                json={"challenge_id": state["challenge_id"], "choice": choice},
            )
            assert response.status_code == 200
            state = wait_state("awaiting_code")
            assert control.calls == ["password", "approval-check"]
            assert control.choice == choice
            log = client.get(f"/api/v1/account-connections/{result['id']}/log")
            assert log.status_code == 200
            assert "synthetic-private-context" not in log.text
            assert "synthetic-private-password" not in log.text
            events = log.json()["events"]
            screens = next(item for item in events if item["event"] == "verification_screens")
            assert screens["detail"] == {
                "stage": "entrypoint",
                "screens": ["com.bloks.www.synthetic.approval"],
                "session": False,
            }
            assert {"event": "owner_choice", "choice": choice} in [
                {"event": item["event"], **item["detail"]} for item in events
            ]
            assert log.json()["request_count"] == 2
            client.post(
                f"/api/v1/account-connections/{result['id']}/code",
                json={"challenge_id": state["challenge_id"], "code": "654321"},
            )
        elif completion == "cancel":
            client.post(f"/api/v1/account-connections/{result['id']}/cancel", json={})
        else:
            with Session(engine) as db, db.begin():
                db.get(AccountConnection, result["id"]).expires_at = now() - timedelta(seconds=1)
            wait_state("expired")
        thread.join(8)
        assert not thread.is_alive()
        wait_state(
            {
                "continue": "connected",
                "continue_code": "connected",
                "cancel": "cancelled",
                "expire": "expired",
            }[completion]
        )
        assert control.calls.count("password") == 1
        assert control.calls.count("approval-check") == (
            1 if completion.startswith("continue") else 0
        )
        with Session(engine) as db:
            assert db.scalar(select(BudgetUsage)).reserved == 0
            row = db.get(AccountConnection, result["id"])
            assert row.password_envelope is None and row.code_envelope is None
    finally:
        stop.set()
        thread.join(5)
