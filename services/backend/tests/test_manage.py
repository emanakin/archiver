"""Owner connection/recovery with synthetic credentials and no network client."""

import os
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import manage
from app.execution_errors import (
    OperationBlocked,
    OwnershipLost,
    ProfileContinuationUnavailable,
    Restriction,
    SessionRequired,
)
from app.models import (
    Account,
    AccountSession,
    ActionAttempt,
    AttemptEvent,
    BudgetReservation,
    BudgetUsage,
    Job,
)
from app.session_store import SessionStore

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_POSTGRES_URL"), reason="Requires isolated PostgreSQL schema"
)


def prepare(postgres, monkeypatch, tmp_path):
    engine, _ = postgres
    key = tmp_path / "key"
    key.write_bytes(Fernet.generate_key())
    configuration = SimpleNamespace(
        adapter_mode="read_only", session_store_path=tmp_path / "ciphertext", session_key_file=key
    )
    monkeypatch.setattr(manage, "engine", engine)
    monkeypatch.setattr(manage, "settings", lambda: configuration)
    monkeypatch.setattr(manage, "verify_installation", lambda: {"version": "3.2.15"})
    clients = []
    control = SimpleNamespace(fail=None)

    def factory(guard, restored):
        class Client:
            password = None

            def login(self, username, password, verification_code):
                self.password = password
                guard.before_request()
                if control.fail == "restriction":
                    raise Restriction("Synthetic manual recovery condition")
                if control.fail == "unsupported":
                    raise ProfileContinuationUnavailable("Synthetic unsupported continuation")
                if control.fail == "manual":
                    assert not guard.lane.connection.in_transaction()
                    assert guard.profile_code_provider() == "654321"
                if control.fail == "mfa":
                    assert not guard.lane.connection.in_transaction()
                    assert guard.mfa_code_provider() == "654321"

            def account_info(self):
                guard.before_request()
                return SimpleNamespace(
                    pk="999" if control.fail == "mismatch" else "12345", username="synthetic.owner"
                )

            def get_settings(self):
                return {"device_id": "synthetic-device", "generation": len(clients)}

        client = Client()
        clients.append(client)
        return client

    monkeypatch.setattr(manage, "build_client", factory)
    account_id = manage.create_account("synthetic.owner", 30)
    return engine, account_id, configuration, clients, control


def test_connection_and_restart_verification_keep_owner_controls_and_count_requests(
    postgres, monkeypatch, tmp_path, capsys
):
    engine, account_id, configuration, clients, control = prepare(postgres, monkeypatch, tmp_path)
    manage.connect_account(
        account_id, password="synthetic-password", verification_code="synthetic-code"
    )
    assert clients[0].password is None
    with Session(engine) as db:
        account, metadata = db.get(Account, account_id), db.get(AccountSession, account_id)
        assert (
            account.paused
            and not account.live_acquisition_enabled
            and not account.live_cleanup_enabled
        )
        assert account.instagram_id == "12345" and account.session_state == "connected"
        assert metadata.capabilities == {"account_verified": True}
        revision = metadata.revision
    engine.dispose()
    store = SessionStore(configuration.session_store_path, configuration.session_key_file)
    assert store.load(account_id, revision)["device_id"] == "synthetic-device"
    manage.connect_account(account_id, verify=True)
    with Session(engine) as db:
        assert db.get(Account, account_id).paused
        assert db.get(AccountSession, account_id).revision != revision
        assert (
            db.scalar(select(BudgetUsage).where(BudgetUsage.category == "requests")).consumed == 3
        )
        assert sorted(attempt.request_count for attempt in db.scalars(select(ActionAttempt))) == [
            1,
            2,
        ]
    output = capsys.readouterr().out
    assert "synthetic-password" not in output and "synthetic-code" not in output


@pytest.mark.parametrize(
    "failure,exception", [("restriction", Restriction), ("mismatch", SessionRequired)]
)
def test_failed_recovery_preserves_active_and_pending_encrypted_revisions(
    postgres, monkeypatch, tmp_path, failure, exception
):
    engine, account_id, configuration, clients, control = prepare(postgres, monkeypatch, tmp_path)
    manage.connect_account(account_id, password="synthetic-password")
    with Session(engine) as db:
        original = db.get(AccountSession, account_id).revision
    control.fail = failure
    with pytest.raises(exception):
        manage.connect_account(account_id, password="synthetic-password", recover=True)
    assert clients[-1].password is None
    with Session(engine) as db:
        metadata = db.get(AccountSession, account_id)
        assert metadata.revision == original and metadata.pending_revision != original
        pending = metadata.pending_revision
        assert db.get(Account, account_id).paused
    store = SessionStore(configuration.session_store_path, configuration.session_key_file)
    assert store.load(account_id, original)["generation"] == 1
    assert store.load(account_id, pending)["generation"] == 2


@pytest.mark.parametrize("prior_restriction", [False, True])
def test_unsupported_phone_continuation_does_not_invent_or_erase_restriction(
    postgres, monkeypatch, tmp_path, prior_restriction
):
    engine, account_id, _, _, control = prepare(postgres, monkeypatch, tmp_path)
    with Session(engine) as db, db.begin():
        account = db.get(Account, account_id)
        account.restricted = prior_restriction
        account.automatic_pause_reason = "ACCOUNT_RESTRICTED" if prior_restriction else None
    control.fail = "unsupported"
    with pytest.raises(ProfileContinuationUnavailable):
        manage.connect_account(account_id, password="synthetic-password", recover=True)
    with Session(engine) as db:
        account = db.get(Account, account_id)
        assert account.restricted is prior_restriction
        assert account.session_state == "needs_verification" and account.paused
        assert account.automatic_pause_reason == (
            "ACCOUNT_RESTRICTED" if prior_restriction else "SESSION_REQUIRED"
        )
        assert db.scalar(select(Job)).error == "PROFILE_CONTINUATION_UNAVAILABLE"


def test_manual_code_checkpoint_does_not_charge_or_store_the_code(
    postgres, monkeypatch, tmp_path, capsys
):
    engine, account_id, configuration, clients, control = prepare(postgres, monkeypatch, tmp_path)
    control.fail = "manual"
    prompts = []

    def private_input(prompt):
        prompts.append(prompt)
        with Session(engine) as db:
            usage = db.scalar(select(BudgetUsage))
            reservation = db.scalar(select(BudgetReservation))
            assert usage.consumed == 1 and usage.reserved == 9
            assert reservation.remaining == 9
            assert db.scalar(select(Job)).lease_expires_at is not None
            assert db.scalar(
                select(AttemptEvent).where(AttemptEvent.event == "owner_code_requested")
            )
        return "654321"

    manage.connect_account(
        account_id,
        password="synthetic-password",
        recover=True,
        manual_profile_verification=True,
        profile_code_provider=private_input,
    )
    assert len(prompts) == 1
    with Session(engine) as db:
        assert db.scalar(select(BudgetUsage)).consumed == 2
        assert db.scalar(select(BudgetUsage)).reserved == 0
        events = list(db.scalars(select(AttemptEvent)))
        assert {e.event for e in events} >= {"owner_code_requested", "owner_code_received"}
        assert all("654321" not in str(e.detail) for e in events)
        assert "654321" not in str(db.scalar(select(Job)).payload)
    assert "654321" not in capsys.readouterr().out


def test_manual_profile_mode_requires_explicit_recovery(postgres, monkeypatch, tmp_path):
    prepare(postgres, monkeypatch, tmp_path)
    with pytest.raises(OperationBlocked):
        manage.connect_account("unused", manual_profile_verification=True)


def test_standard_mfa_prompt_runs_only_after_first_request(postgres, monkeypatch, tmp_path):
    engine, account_id, configuration, clients, control = prepare(postgres, monkeypatch, tmp_path)
    control.fail = "mfa"
    prompts = []

    def private_input(prompt):
        prompts.append(prompt)
        with Session(engine) as db:
            assert db.scalar(select(BudgetUsage)).consumed == 1
        return "654321"

    manage.connect_account(
        account_id, password="synthetic-password", mfa_code_provider=private_input
    )
    assert len(prompts) == 1
    with Session(engine) as db:
        assert db.scalar(select(BudgetUsage)).consumed == 2
        assert db.get(Account, account_id).paused
        assert db.scalar(select(Job)).payload["interactive_mfa"] is True
        assert "654321" not in str(db.scalar(select(Job)).payload)
        assert {e.event for e in db.scalars(select(AttemptEvent))} >= {
            "owner_code_requested",
            "owner_code_received",
        }


def test_lost_claim_during_owner_input_stops_before_any_more_http(postgres, monkeypatch, tmp_path):
    engine, account_id, configuration, clients, control = prepare(postgres, monkeypatch, tmp_path)
    control.fail = "manual"

    def private_input(_prompt):
        with Session(engine) as db, db.begin():
            db.scalar(select(Job)).claim_owner = "another-owner"
        return "654321"

    with pytest.raises(OwnershipLost):
        manage.connect_account(
            account_id,
            password="synthetic-password",
            recover=True,
            manual_profile_verification=True,
            profile_code_provider=private_input,
        )
    with Session(engine) as db:
        assert db.scalar(select(ActionAttempt)).request_count == 1
        assert not db.scalar(
            select(AttemptEvent).where(AttemptEvent.event == "owner_code_received")
        )
