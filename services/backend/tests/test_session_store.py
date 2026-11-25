import uuid

import pytest
from cryptography.fernet import Fernet

from app.execution_errors import SessionRequired
from app.session_store import SessionStore


def test_encrypted_immutable_session_revisions(tmp_path):
    key = tmp_path / "key"
    key.write_bytes(Fernet.generate_key())
    store = SessionStore(tmp_path / "sessions", key)
    account_id = str(uuid.uuid4())
    first = store.save(account_id, {"cookies": {"sessionid": "synthetic-private-session"}})
    ciphertext = store.path(account_id, first).read_bytes()
    assert b"synthetic-private-session" not in ciphertext
    assert store.load(account_id, first)["cookies"]["sessionid"] == "synthetic-private-session"
    second = store.save(account_id, {"cookies": {"sessionid": "synthetic-new-session"}})
    assert first != second and store.path(account_id, first).read_bytes() == ciphertext
    wrong_key = tmp_path / "other-key"
    wrong_key.write_bytes(Fernet.generate_key())
    with pytest.raises(SessionRequired):
        SessionStore(tmp_path / "sessions", wrong_key).load(account_id, first)
    store.path(account_id, second).write_bytes(b"corrupt")
    with pytest.raises(SessionRequired):
        store.load(account_id, second)


def test_session_paths_reject_traversal_and_missing_key(tmp_path):
    with pytest.raises(SessionRequired):
        SessionStore(tmp_path, tmp_path / "missing-key")
    key = tmp_path / "key"
    key.write_bytes(Fernet.generate_key())
    store = SessionStore(tmp_path / "sessions", key)
    with pytest.raises(ValueError):
        store.save("../outside", {"value": "sample"})
    assert not (tmp_path / "outside").exists()
