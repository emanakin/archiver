"""Browser connection executor. Only this worker can decrypt submitted secrets."""

import time
import uuid
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import manage
from .config import settings
from .execution_errors import ExecutionError, SessionRequired
from .executor import aware
from .models import AccountConnection, OnboardingKey, now
from .onboarding_crypto import key_id, public_key, unseal, worker_private_key
from .onboarding_routes import ACTIVE, clear_secrets, expire


class AccountOnboardingWorker:
    def __init__(self, engine, stop, connector=None, key_path=None):
        self.engine, self.stop = engine, stop
        self.connector = connector or manage.connect_account
        self.private = worker_private_key(key_path or settings().session_key_file)
        self.public = public_key(self.private)
        self.key_id = key_id(self.public)
        self.owner = str(uuid.uuid4())

    def publish_key(self):
        with Session(self.engine) as db, db.begin():
            db.merge(OnboardingKey(id=self.key_id, public_key=self.public, observed_at=now()))

    def reap(self):
        with Session(self.engine) as db, db.begin():
            for connection in db.scalars(
                select(AccountConnection)
                .where(AccountConnection.state.in_(ACTIVE))
                .with_for_update()
            ):
                expire(connection)
                if (
                    connection.state in ACTIVE
                    and connection.state != "queued"
                    and aware(connection.heartbeat_at) < now() - timedelta(seconds=90)
                ):
                    connection.state, connection.error_code = "failed", "WORKER_RESTARTED"
                    clear_secrets(connection)

    def checked(self, db, connection_id):
        connection = db.scalar(
            select(AccountConnection).where(AccountConnection.id == connection_id).with_for_update()
        )
        if connection is None or connection.owner != self.owner:
            raise SessionRequired("Connection owner changed")
        expire(connection)
        if self.stop.is_set() or connection.state not in ACTIVE:
            raise SessionRequired("Browser connection ended")
        connection.heartbeat_at = now()
        return connection

    def check(self, connection_id):
        with Session(self.engine) as db, db.begin():
            self.checked(db, connection_id)

    def request_code(self, connection_id, kind, heartbeat=None):
        with Session(self.engine) as db, db.begin():
            connection = self.checked(db, connection_id)
            if kind != "approval" and connection.code_submitted:
                raise SessionRequired("A verification code has already been submitted")
            connection.challenge_kind, connection.challenge_id = kind, str(uuid.uuid4())
            connection.state = "awaiting_approval" if kind == "approval" else "awaiting_code"
        last_heartbeat = 0.0
        while not self.stop.wait(0.5):
            # Keep the account claim and request reservation alive during human
            # input. Browser polling alone must not let the job lease expire.
            if heartbeat and time.monotonic() - last_heartbeat >= 15:
                heartbeat()
                last_heartbeat = time.monotonic()
            with Session(self.engine) as db, db.begin():
                connection = self.checked(db, connection_id)
                if kind == "approval" and connection.state == "verifying":
                    return "code" if connection.challenge_kind == "approval_code" else "approved"
                if connection.code_envelope:
                    code = unseal(
                        self.private,
                        connection.code_envelope,
                        f"{connection.id}:{connection.account_id}:code",
                    )
                    connection.code_envelope = None
                    connection.state = "verifying"
                    return code
        raise SessionRequired("Worker stopped while awaiting verification")

    def run_once(self):
        self.reap()
        with Session(self.engine) as db, db.begin():
            connection = db.scalar(
                select(AccountConnection)
                .where(AccountConnection.state == "queued")
                .order_by(AccountConnection.created_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if connection is None:
                return False
            connection.state, connection.owner, connection.heartbeat_at = (
                "connecting",
                self.owner,
                now(),
            )
            connection_id, account_id, recovering, maximum = (
                connection.id,
                connection.account_id,
                connection.recovery_acknowledged,
                connection.max_requests,
            )
            envelope, encryption_key_id = connection.password_envelope, connection.key_id
            connection.password_envelope = None
        password = None
        try:
            if encryption_key_id != self.key_id or not envelope:
                raise SessionRequired("Connection key changed; reconnect")
            password = unseal(self.private, envelope, f"{connection_id}:{account_id}:password")

            def started(job_id):
                with Session(self.engine) as db, db.begin():
                    self.checked(db, connection_id).job_id = job_id

            def verified(db):
                row = self.checked(db, connection_id)
                row.state, row.error_code = "connected", None
                clear_secrets(row)

            self.connector(
                account_id,
                password=password,
                recover=recovering,
                max_requests=maximum,
                manual_profile_verification=True,
                verification_waiter=lambda kind, heartbeat: self.request_code(
                    connection_id, kind, heartbeat
                ),
                connection_check=lambda: self.check(connection_id),
                on_started=started,
                on_verified=verified,
            )
        except Exception as exc:
            with Session(self.engine) as db, db.begin():
                connection = db.scalar(
                    select(AccountConnection)
                    .where(AccountConnection.id == connection_id)
                    .with_for_update()
                )
                if connection and connection.owner == self.owner:
                    if connection.state in ACTIVE:
                        connection.state = "failed"
                        connection.error_code = (
                            exc.code if isinstance(exc, ExecutionError) else "CONNECTION_FAILED"
                        )
                    clear_secrets(connection)
        finally:
            password = envelope = None
        return True

    def run(self):
        while not self.stop.is_set():
            try:
                self.run_once()
            except Exception:
                # Never print exceptions from secret transport or Instagram.
                pass
            self.stop.wait(1)
