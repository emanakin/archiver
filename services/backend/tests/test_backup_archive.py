import io
import os
import sys
import tarfile
import uuid
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

script_directory = os.getenv("ARCHIVE_SCRIPTS_DIR") or str(
    Path(__file__).resolve().parents[3] / "scripts"
)
sys.path.insert(0, script_directory)
from backup_support import validate_content, validate_sessions  # noqa: E402


def write_archive(path, name, data):
    with tarfile.open(path, "w") as archive:
        entry = tarfile.TarInfo(name)
        entry.size = len(data)
        archive.addfile(entry, io.BytesIO(data))


def test_restored_database_session_pointer_requires_matching_ciphertext_and_key(tmp_path):
    account, revision = str(uuid.uuid4()), str(uuid.uuid4())
    key = Fernet.generate_key()
    key_file = tmp_path / "separate-key"
    key_file.write_bytes(key)
    archive = tmp_path / "sessions.tar"
    write_archive(
        archive, f"./{account}.{revision}.fernet", Fernet(key).encrypt(b'{"synthetic":true}')
    )
    assert validate_sessions(archive, [(account, revision)], key_file) == 1
    with pytest.raises(ValueError, match="missing"):
        validate_sessions(archive, [(account, str(uuid.uuid4()))], key_file)


@pytest.mark.parametrize("name", ["../key", "/tmp/key", "C:/key", "folder\\key"])
def test_restore_drill_rejects_unsafe_paths_without_extracting(tmp_path, name):
    archive = tmp_path / "unsafe.tar"
    write_archive(archive, name, b"untrusted")
    with pytest.raises(ValueError, match="Unsafe"):
        validate_sessions(archive, [])
    with pytest.raises(ValueError, match="Unsafe"):
        validate_content(archive, [])


def test_content_restore_checks_referenced_bytes_and_never_extracts(tmp_path):
    import hashlib

    identifier = str(uuid.uuid4())
    data = b"immutable synthetic image bytes"
    archive = tmp_path / "content.tar"
    write_archive(archive, f"./{identifier}.original", data)
    digest = hashlib.sha256(data).hexdigest()
    assert validate_content(archive, [(identifier, digest, len(data), "image/jpeg")]) == 1
    with pytest.raises(ValueError, match="match"):
        validate_content(archive, [(identifier, "0" * 64, len(data), "image/jpeg")])
    with pytest.raises(ValueError, match="missing"):
        validate_content(archive, [(str(uuid.uuid4()), digest, len(data), "image/jpeg")])


def test_backup_restore_accepts_larger_mp4_but_rejects_it_as_an_image(tmp_path):
    from hashlib import sha256
    from types import SimpleNamespace

    from app.content_backup import restore_originals

    data = b"\0\0\0\x18ftyp" + b"\0" * (8 * 1024 * 1024)
    asset = SimpleNamespace(
        id=str(uuid.uuid4()),
        sha256=sha256(data).hexdigest(),
        byte_size=len(data),
        mime="video/mp4",
        storage_backend="local",
    )
    archive = tmp_path / "content.tar"
    write_archive(archive, f"{asset.id}.original", data)
    config = SimpleNamespace(content_store_path=str(tmp_path / "restored"))
    assert validate_content(archive, [(asset.id, asset.sha256, asset.byte_size, asset.mime)]) == 1
    restore_originals(archive, config, [asset])
    assert (Path(config.content_store_path) / f"{asset.id}.original").read_bytes() == data

    asset.mime = "image/jpeg"
    with pytest.raises(ValueError, match="media limit"):
        validate_content(archive, [(asset.id, asset.sha256, asset.byte_size, asset.mime)])
    with pytest.raises(ValueError, match="Invalid original"):
        restore_originals(archive, config, [asset])
