"""Local owner setup: create an encryption key without overwriting it."""
from pathlib import Path
import os
from cryptography.fernet import Fernet

destination = Path(__file__).resolve().parents[1] / "secrets/instagram_session_key"
destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(descriptor, "wb") as file:
    file.write(Fernet.generate_key())
print("Created the worker session key. Keep a separate private backup; it is not printed here.")
