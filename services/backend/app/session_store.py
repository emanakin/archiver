"""Encrypted immutable session revisions, readable only by the worker."""

import json
import os
import tempfile
import uuid
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from .execution_errors import SessionRequired


class SessionStore:
    def __init__(self, directory, key_file):
        self.directory = Path(directory).resolve()
        try:
            self.cipher = Fernet(Path(key_file).read_bytes().strip())
        except (OSError, ValueError):
            raise SessionRequired("Worker session key is unavailable or invalid") from None

    def path(self, account_id, revision):
        account_id, revision = str(uuid.UUID(account_id)), str(uuid.UUID(revision))
        return self.directory / f"{account_id}.{revision}.fernet"

    def save(self, account_id, values):
        data = json.dumps(values, separators=(",", ":")).encode()
        if len(data) > 512 * 1024:
            raise SessionRequired("Session exceeds the allowed storage size")
        revision = str(uuid.uuid4())
        destination = self.path(account_id, revision)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.directory, 0o700)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=self.directory, prefix=".session-", delete=False
            ) as file:
                temporary = Path(file.name)
                os.chmod(file.name, 0o600)
                file.write(self.cipher.encrypt(data))
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, destination)
            if os.name != "nt":
                descriptor = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
        finally:
            if temporary and temporary.exists():
                temporary.unlink()
        return revision

    def load(self, account_id, revision):
        try:
            encrypted = self.path(account_id, revision).read_bytes()
            if len(encrypted) > 1024 * 1024:
                raise ValueError("oversized")
            decoded = json.loads(self.cipher.decrypt(encrypted))
            if not isinstance(decoded, dict):
                raise ValueError("invalid shape")
            return decoded
        except (OSError, ValueError, InvalidToken):
            raise SessionRequired(
                "Stored session cannot be restored; manual recovery is required"
            ) from None
