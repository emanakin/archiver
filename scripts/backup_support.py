"""Offline integrity checks shared by backup and the isolated restore drill."""

import hashlib
import json
import re
import tarfile
from pathlib import Path, PurePosixPath


def file_information(path):
    with Path(path).open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return {"bytes": Path(path).stat().st_size, "sha256": digest}


def validate_manifest(directory):
    directory = Path(directory)
    manifest = json.loads((directory / "manifest.json").read_text())
    files = manifest["files"]
    if "database.dump" not in files or set(files) - {"database.dump", "encrypted-sessions.tar", "content-assets.tar"}:
        raise ValueError("Unexpected or missing backup manifest entry")
    for name, information in files.items():
        if (directory / name).is_symlink() or file_information(directory / name) != information:
            raise ValueError("Backup integrity check failed")
    if manifest.get("key_included") is not False:
        raise ValueError("Session encryption keys must be stored separately")
    return manifest


def validate_sessions(path, references, key_file=None):
    """Verify DB-referenced immutable revisions without extracting archive paths."""
    expected = {f"{account}.{revision}.fernet" for account, revision in references}
    validated = set()
    cipher = None
    if key_file:
        from cryptography.fernet import Fernet
        cipher = Fernet(Path(key_file).read_bytes().strip())
    with tarfile.open(path) as archive:
        seen = set()
        for entry in archive:
            name = PurePosixPath(entry.name)
            if name.is_absolute() or ".." in name.parts or ":" in entry.name or "\\" in entry.name:
                raise ValueError("Unsafe session archive member")
            if entry.isdir() and str(name) == ".":
                continue
            if not entry.isfile() or len(name.parts) != 1 or entry.size > 1024 * 1024:
                raise ValueError("Unsafe session archive member")
            if str(name) in seen:
                raise ValueError("Duplicate session archive member")
            seen.add(str(name))
            # Temp files from an interrupted atomic save have no DB pointer.
            if str(name).startswith(".session-"):
                continue
            if not re.fullmatch(r"[a-f0-9-]{36}\.[a-f0-9-]{36}\.fernet", str(name)):
                raise ValueError("Unexpected session archive member")
            if str(name) in expected:
                if cipher:
                    content = archive.extractfile(entry).read()
                    decoded = json.loads(cipher.decrypt(content))
                    if not isinstance(decoded, dict):
                        raise ValueError("Invalid encrypted session shape")
                validated.add(str(name))
    if validated != expected:
        raise ValueError("The session archive is missing a database-referenced revision")
    return len(validated)


def validate_content(path, references):
    """Check immutable asset bytes against restored DB hashes; never extract."""
    expected = {
        f"{identifier}.original": (digest, int(size), mime)
        for identifier, digest, size, mime in references
    }
    validated, seen = set(), set()
    with tarfile.open(path) as archive:
        for entry in archive:
            name = PurePosixPath(entry.name)
            if entry.isdir() and str(name) == ".":
                continue
            if (name.is_absolute() or len(name.parts) != 1 or ".." in name.parts
                or not entry.isfile() or not 0 < entry.size <= 32 * 1024 * 1024
                or not re.fullmatch(r"[a-f0-9-]{36}\.original", str(name))
                or str(name) in seen):
                raise ValueError("Unsafe or duplicate content archive member")
            seen.add(str(name))
            if str(name) in expected:
                digest, size, mime = expected[str(name)]
                limit = (32 if mime == "video/mp4" else 8) * 1024 * 1024
                if entry.size != size or entry.size > limit:
                    raise ValueError("Content asset size does not match the restored database or media limit")
                with archive.extractfile(entry) as original:
                    if hashlib.file_digest(original, "sha256").hexdigest() != digest:
                        raise ValueError("Content asset does not match the restored database")
                validated.add(str(name))
    if validated != set(expected):
        raise ValueError("The content archive is missing a referenced asset")
    return len(validated)
