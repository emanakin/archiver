"""Restore into a generated disposable database; never replace the application DB."""
import argparse
from pathlib import Path
import re
import subprocess
import uuid
from backup_support import validate_manifest, validate_sessions, validate_content

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("backup_directory")
parser.add_argument("--env-file", default=".env")
parser.add_argument("--session-key", help="Separate local key file; verifies decryption without displaying session data")
args = parser.parse_args()
directory = Path(args.backup_directory).resolve()
manifest = validate_manifest(directory)
compose = ["docker", "compose", "--env-file", args.env_file]
def run(*arguments):
    return subprocess.run([*compose, *arguments], cwd=root, check=True, capture_output=True, text=True).stdout.strip()
database = "restore_" + uuid.uuid4().hex[:12]
assert re.fullmatch(r"restore_[a-f0-9]{12}", database) and database != "archive"
temporary = "/tmp/archive-restore-" + uuid.uuid4().hex + ".dump"
try:
    run("exec", "-T", "db", "createdb", "-U", "archive", database)
    run("cp", str(directory / "database.dump"), "db:" + temporary)
    run("exec", "-T", "db", "pg_restore", "-U", "archive", "--no-owner", "--exit-on-error", "-d", database, temporary)
    revision = run("exec", "-T", "db", "psql", "-U", "archive", "-d", database, "-Atc", "SELECT version_num FROM alembic_version")
    if revision != manifest["schema_revision"]:
        raise SystemExit("Restored schema revision does not match the backup")
    count = int(run("exec", "-T", "db", "psql", "-U", "archive", "-d", database, "-Atc", "SELECT count(*) FROM accounts"))
    # Exercise the quarantine required before any restored environment is used.
    run("exec", "-T", "db", "psql", "-v", "ON_ERROR_STOP=1", "-U", "archive", "-d", database, "-c", "UPDATE accounts SET paused=true, live_acquisition_enabled=false, live_cleanup_enabled=false, read_only_verified_at=NULL; UPDATE jobs SET state='unknown', error='OUTCOME_UNKNOWN' WHERE state='running' AND dispatch_started_at IS NOT NULL; UPDATE jobs SET state='blocked', error='RESTORE_REQUIRES_REVIEW' WHERE state IN ('queued','running') AND dispatch_started_at IS NULL;")
    unpaused = int(run("exec", "-T", "db", "psql", "-U", "archive", "-d", database, "-Atc", "SELECT count(*) FROM accounts WHERE paused=false OR live_acquisition_enabled=true OR live_cleanup_enabled=true"))
    assert unpaused == 0
    if run("exec", "-T", "db", "psql", "-U", "archive", "-d", database, "-Atc", "SELECT to_regclass('account_connections') IS NOT NULL") == "t":
        run("exec", "-T", "db", "psql", "-v", "ON_ERROR_STOP=1", "-U", "archive", "-d", database, "-c", "UPDATE account_connections SET state='cancelled', error_code='RESTORE_REQUIRES_REVIEW' WHERE state IN ('queued','connecting','awaiting_code','awaiting_approval','verifying'); UPDATE account_connections SET password_envelope=NULL, code_envelope=NULL; DELETE FROM onboarding_keys;")
        remaining = int(run("exec", "-T", "db", "psql", "-U", "archive", "-d", database, "-Atc", "SELECT count(*) FROM account_connections WHERE password_envelope IS NOT NULL OR code_envelope IS NOT NULL"))
        assert remaining == 0
    references = run("exec", "-T", "db", "psql", "-U", "archive", "-d", database, "-Atc", "SELECT account_id || '|' || revision FROM account_sessions UNION SELECT account_id || '|' || pending_revision FROM account_sessions WHERE pending_revision IS NOT NULL")
    revisions = [tuple(line.split("|")) for line in references.splitlines() if line]
    if "encrypted-sessions.tar" in manifest["files"]:
        validated = validate_sessions(directory / "encrypted-sessions.tar", revisions, args.session_key)
        print("PASS: referenced encrypted revisions present:", validated, "; decryption:", "verified" if args.session_key else "not checked (supply --session-key)")
    elif revisions:
        print("Session recovery NOT VERIFIED: this database references sessions absent from this backup.")
    if run("exec", "-T", "db", "psql", "-U", "archive", "-d", database, "-Atc", "SELECT to_regclass('content_assets') IS NOT NULL") == "t":
        run("exec", "-T", "db", "psql", "-v", "ON_ERROR_STOP=1", "-U", "archive", "-d", database, "-c", "UPDATE insight_watches SET enabled=false, version=version+1; UPDATE relationship_tests SET state='review_required', error_code='RESTORE_REQUIRES_REVIEW' WHERE state IN ('follow_pending','unfollow_pending'); UPDATE content_drafts d SET status=CASE WHEN j.dispatch_started_at IS NOT NULL THEN 'unknown' ELSE 'failed' END FROM jobs j WHERE d.publication_job_id=j.id AND d.status='queued' AND j.state IN ('blocked','unknown');")
        assets = run("exec", "-T", "db", "psql", "-U", "archive", "-d", database, "-Atc", "SELECT id || '|' || sha256 || '|' || byte_size || '|' || mime FROM content_assets")
        references = [tuple(line.split("|")) for line in assets.splitlines() if line]
        if "content-assets.tar" in manifest["files"]:
            print("PASS: immutable content assets verified:", validate_content(directory / "content-assets.tar", references))
        elif references:
            print("Content recovery NOT VERIFIED: supply a backup with --include-content.")
    if run("exec", "-T", "db", "psql", "-U", "archive", "-d", database, "-Atc", "SELECT to_regclass('dm_inboxes') IS NOT NULL") == "t":
        run("exec", "-T", "db", "psql", "-v", "ON_ERROR_STOP=1", "-U", "archive", "-d", database, "-c", "UPDATE dm_inboxes SET enabled=false, version=version+1;")
        if run("exec", "-T", "db", "psql", "-U", "archive", "-d", database, "-Atc", "SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='dm_inboxes' AND column_name='direct_publish_from')") == "t":
            run("exec", "-T", "db", "psql", "-v", "ON_ERROR_STOP=1", "-U", "archive", "-d", database, "-c", "UPDATE dm_inboxes SET direct_publish_from=NULL;")
    print("PASS: backup checksums, isolated database restore and paused workflow quarantine; schema", revision, "accounts", count)
finally:
    # Both identifiers were generated above and cannot name production storage.
    run("exec", "-T", "db", "rm", "-f", temporary)
    run("exec", "-T", "db", "dropdb", "-U", "archive", "--if-exists", database)
