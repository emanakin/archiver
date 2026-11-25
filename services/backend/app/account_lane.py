"""Session-level PostgreSQL ownership, held without an open transaction."""

import hashlib
import uuid

from sqlalchemy import text

from .execution_errors import OwnershipLost


class AccountLane:
    def __init__(self, engine, account_id):
        if engine.dialect.name != "postgresql":
            raise RuntimeError("Durable execution requires PostgreSQL account locks")
        self.engine = engine
        self.account_id = account_id
        self.key = int.from_bytes(
            hashlib.sha256(account_id.encode()).digest()[:8], "big", signed=True
        )
        self.owner = str(uuid.uuid4())
        self.connection = None
        self.backend_pid = None
        self.acquired = False

    def __enter__(self):
        self.connection = self.engine.connect()
        try:
            self.backend_pid, self.acquired = self.connection.execute(
                text("SELECT pg_backend_pid(), pg_try_advisory_lock(:key)"), {"key": self.key}
            ).one()
            self.connection.commit()
            return self
        except BaseException:
            self.connection.close()
            raise

    def assert_owned(self):
        connection = self.connection
        if not self.acquired or connection is None or connection.closed or connection.invalidated:
            raise OwnershipLost("Account request lane ownership was lost")
        try:
            pid = connection.scalar(text("SELECT pg_backend_pid()"))
            connection.commit()
            if pid != self.backend_pid:
                raise OwnershipLost("Account lock connection changed")
        except Exception as exc:
            self.acquired = False
            connection.invalidate()
            raise OwnershipLost("Account request lane ownership was lost") from exc

    def __exit__(self, *_):
        if self.connection is None:
            return
        try:
            if self.acquired and not self.connection.invalidated and not self.connection.closed:
                self.connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": self.key})
                self.connection.commit()
        except BaseException:
            self.connection.invalidate()
            raise
        finally:
            # A broken connection must never return to the pool holding an unknown lock.
            if self.connection.in_transaction():
                self.connection.rollback()
            if not self.acquired and not self.connection.invalidated and not self.connection.closed:
                self.connection.invalidate()
            self.connection.close()
            self.acquired = False
