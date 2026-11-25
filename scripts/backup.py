"""Cross-platform pg_dump/ciphertext backup without binary shell redirection."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import uuid
from backup_support import file_information

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("--env-file", default=".env")
parser.add_argument("--include-sessions", action="store_true")
parser.add_argument("--include-content", action="store_true")
args = parser.parse_args()
compose = ["docker", "compose", "--env-file", args.env_file]

def run(*arguments):
    return subprocess.run([*compose, *arguments], cwd=root, check=True, capture_output=True, text=True).stdout.strip()

stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
destination = root / "backups" / stamp
destination.mkdir(parents=True, mode=0o700)
temporary = "/tmp/archive-backup-" + uuid.uuid4().hex + ".dump"
try:
    run("exec", "-T", "db", "pg_dump", "-U", "archive", "-d", "archive", "-Fc", "-f", temporary)
    run("cp", "db:" + temporary, str(destination / "database.dump"))
finally:
    run("exec", "-T", "db", "rm", "-f", temporary)
files = [destination / "database.dump"]
revision = run("exec", "-T", "db", "psql", "-U", "archive", "-d", "archive", "-Atc", "SELECT version_num FROM alembic_version")
if args.include_sessions:
    temporary = "/tmp/archive-sessions-" + uuid.uuid4().hex + ".tar"
    try:
        run("exec", "-T", "worker", "tar", "-C", "/var/lib/archive-desk/sessions", "-cf", temporary, ".")
        run("cp", "worker:" + temporary, str(destination / "encrypted-sessions.tar"))
    finally:
        run("exec", "-T", "worker", "rm", "-f", temporary)
    files.append(destination / "encrypted-sessions.tar")
if args.include_content:
    temporary = "/tmp/archive-content-" + uuid.uuid4().hex + ".tar"
    try:
        if int(revision) >= 13:
            run("exec", "-T", "api", "python", "-m", "app.content_backup", "export", temporary)
        else:
            run("exec", "-T", "api", "tar", "-C", "/var/lib/archive-desk/content", "-cf", temporary, ".")
        run("cp", "api:" + temporary, str(destination / "content-assets.tar"))
    finally:
        run("exec", "-T", "api", "rm", "-f", temporary)
    files.append(destination / "content-assets.tar")
manifest = {"created_at": stamp, "schema_revision": revision,
    "files": {file.name: file_information(file) for file in files}, "key_included": False}
for file in files:
    os.chmod(file, 0o600)
(destination / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
os.chmod(destination / "manifest.json", 0o600)
print("Created backup:", destination)
print("Copy to encrypted off-host storage. Back up the session encryption key separately.")
