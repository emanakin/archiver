"""Provision private database credentials for the Google production overlay."""

import os
import secrets
import sys
from pathlib import Path

import psycopg
from app.config import settings
from app.db import engine
from app.models import OWNER_WORKSPACE_ID, Account, AccountSettings
from app.schemas import MusicPlaylist
from psycopg import sql
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

ROLES = {
    "ARCHIVE_API_DATABASE_URL": ("archive_api_login", "archive_tenant_request"),
    "ARCHIVE_AUTH_DATABASE_URL": ("archive_auth_login", "archive_identity_request"),
    "ARCHIVE_WORKER_DATABASE_URL": ("archive_worker_login", "archive_worker_data"),
}


def preserve_owner_playlist():
    cfg = settings()
    if not cfg.dm_default_account_id or not cfg.dm_spotify_playlist_id:
        return
    with Session(engine) as db, db.begin():
        account = db.get(Account, cfg.dm_default_account_id, with_for_update=True)
        if not account or account.workspace_id != OWNER_WORKSPACE_ID:
            raise RuntimeError(
                "The existing playlist must be linked to the owner account"
            )
        row = db.get(AccountSettings, account.id)
        values = dict(row.values or {}) if row else {}
        if values.get("music_library") is None:
            values["music_library"] = [
                MusicPlaylist(
                    playlist_id=cfg.dm_spotify_playlist_id, label="Original playlist"
                ).model_dump()
            ]
            if row:
                row.values = values
                row.version += 1
            else:
                db.add(AccountSettings(account_id=account.id, version=1, values=values))


def provision(path):
    base = make_url(settings().database_url)
    if base.drivername != "postgresql+psycopg":
        raise RuntimeError("PostgreSQL is required")
    preserve_owner_playlist()
    created_file = False
    try:
        with psycopg.connect(
            base.set(drivername="postgresql").render_as_string(hide_password=False)
        ) as db:
            db.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended('archive-provision-roles', 0))"
            )
            config = []
            for env, (name, group) in ROLES.items():
                password = secrets.token_urlsafe(36)
                if db.execute(
                    "SELECT 1 FROM pg_roles WHERE rolname = %s", (name,)
                ).fetchone():
                    raise RuntimeError(
                        "A workspace login already exists. Use its saved credential file."
                    )
                db.execute(
                    sql.SQL(
                        "CREATE ROLE {} LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE "
                        "NOREPLICATION NOBYPASSRLS PASSWORD {}"
                    ).format(sql.Identifier(name), sql.Literal(password))
                )
                db.execute(
                    sql.SQL("GRANT {} TO {}").format(
                        sql.Identifier(group), sql.Identifier(name)
                    )
                )
                url = base.set(username=name, password=password).render_as_string(
                    hide_password=False
                )
                config.append(f"{env}={url}")
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            created_file = True
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write("\n".join(config) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
    except BaseException:
        if created_file:
            path.unlink(missing_ok=True)
        raise


if __name__ == "__main__":
    try:
        provision(Path(sys.argv[1]))
    except (IndexError, ValueError, OSError, psycopg.Error, RuntimeError) as exc:
        raise SystemExit(
            f"Workspace role provisioning failed ({type(exc).__name__})."
        ) from None
    print("Workspace database credentials created.")
