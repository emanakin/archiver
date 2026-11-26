"""Add identity and workspace isolation before enabling public signup."""

import sqlalchemy as sa
from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None

OWNER = "00000000-0000-4000-8000-000000000001"
SYSTEM = "00000000-0000-4000-8000-000000000002"
TENANT = "archive_tenant_request"
IDENTITY = "archive_identity_request"
WORKER = "archive_worker_data"
IDENTITY_TABLES = ("users", "workspaces", "web_sessions", "google_login_flows")
ACCOUNT_TABLES = (
    "sources",
    "relationships",
    "jobs",
    "audit_events",
    "account_settings",
    "follow_intents",
    "intent_receipts",
    "action_attempts",
    "budget_usage",
    "budget_reservations",
    "account_sessions",
    "account_connections",
    "insight_checks",
    "insight_watches",
    "relationship_tests",
    "content_assets",
    "content_drafts",
    "dm_inboxes",
    "dm_submissions",
    "saved_audio",
    "playlist_selections",
)
PARENT_TABLES = {
    "discoveries": ("sources", "source_id"),
    "actions": ("jobs", "job_id"),
    "attempt_events": ("action_attempts", "attempt_id"),
    "liker_observations": ("insight_watches", "watch_id"),
}
SCOPED = ("accounts", *ACCOUNT_TABLES, *PARENT_TABLES, "playlist_tracks", "playlist_sync")
PUBLIC_LOOKUPS = ("targets", "onboarding_keys", "worker_heartbeats")
WORKER_TABLES = (*SCOPED, *PUBLIC_LOOKUPS, "username_history")


def install_postgres_policies():
    for role in (TENANT, IDENTITY, WORKER):
        op.execute(f"""
            DO $$ BEGIN CREATE ROLE {role} NOLOGIN;
            EXCEPTION WHEN duplicate_object THEN NULL; END $$;
        """)
    schema = op.get_bind().scalar(sa.text("select current_schema()"))
    quoted = op.get_bind().dialect.identifier_preparer.quote(schema)
    op.execute(f"GRANT USAGE ON SCHEMA {quoted} TO {TENANT}, {IDENTITY}, {WORKER}")
    for role, tables in ((TENANT, SCOPED), (IDENTITY, IDENTITY_TABLES), (WORKER, WORKER_TABLES)):
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {', '.join(tables)} TO {role}")
    op.execute(f"GRANT SELECT ON {', '.join(PUBLIC_LOOKUPS)} TO {TENANT}")
    op.execute(f"GRANT SELECT ON alembic_version TO {TENANT}")
    # Targets are a shared cache of public Instagram handles. Private relationships
    # and discovered links live in the account-scoped tables above.
    op.execute(f"GRANT INSERT ON targets TO {TENANT}")
    for table in SCOPED:
        if table == "accounts":
            predicate = "workspace_id = current_setting('archive.workspace_id', true)"
        elif table in ACCOUNT_TABLES:
            predicate = f"EXISTS (SELECT 1 FROM accounts a WHERE a.id = {table}.account_id)"
        elif table in PARENT_TABLES:
            parent, key = PARENT_TABLES[table]
            predicate = f"EXISTS (SELECT 1 FROM {parent} p WHERE p.id = {table}.{key})"
        else:
            predicate = f"""EXISTS (
                SELECT 1 FROM account_settings a
                CROSS JOIN LATERAL jsonb_array_elements(
                    CASE WHEN jsonb_typeof(a."values"::jsonb->'music_library') = 'array'
                         THEN a."values"::jsonb->'music_library' ELSE '[]'::jsonb END
                ) entry
                WHERE entry->>'playlist_id' = {table}.playlist_id
            )"""
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY workspace_access ON {table} TO {TENANT} "
            f"USING ({predicate}) WITH CHECK ({predicate})"
        )
        op.execute(
            f"CREATE POLICY worker_access ON {table} TO {WORKER} USING (true) WITH CHECK (true)"
        )
    op.execute(f"""
        CREATE FUNCTION limit_workspace_queue() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog, {quoted}
        AS $$
        DECLARE active_workspace varchar(36);
        BEGIN
          IF NOT pg_has_role(session_user, '{TENANT}', 'MEMBER')
             OR NEW.state NOT IN ('queued', 'running') THEN RETURN NEW; END IF;
          IF TG_OP = 'UPDATE' THEN
            IF OLD.state IN ('queued', 'running') THEN RETURN NEW; END IF;
          END IF;
          SELECT workspace_id INTO active_workspace FROM accounts WHERE id = NEW.account_id;
          IF active_workspace IS DISTINCT FROM current_setting('archive.workspace_id', true)
             THEN RETURN NEW; END IF;
          PERFORM pg_advisory_xact_lock(hashtextextended('archive-queue:' || active_workspace, 0));
          IF (SELECT count(*) FROM jobs j JOIN accounts a ON a.id = j.account_id
              WHERE a.workspace_id = active_workspace AND j.state IN ('queued', 'running')) >= 30 THEN
            RAISE EXCEPTION USING ERRCODE = '23514', CONSTRAINT = 'workspace_queue_limit',
                MESSAGE = 'Workspace queue is full';
          END IF;
          RETURN NEW;
        END $$;
    """)
    op.execute("""
        CREATE TRIGGER workspace_queue_limit BEFORE INSERT OR UPDATE OF state ON jobs
        FOR EACH ROW EXECUTE FUNCTION limit_workspace_queue();
    """)


def upgrade():
    op.create_table(
        "users",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("google_subject", sa.String(255), nullable=False, unique=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    workspaces = op.create_table(
        "workspaces",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_user_id", sa.String(36), sa.ForeignKey("users.id"), unique=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.bulk_insert(workspaces, [{"id": OWNER}, {"id": SYSTEM}])
    op.create_table(
        "web_sessions",
        sa.Column("digest", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_web_sessions_user_id", "web_sessions", ["user_id"])
    op.create_index("ix_web_sessions_expires_at", "web_sessions", ["expires_at"])
    op.create_table(
        "google_login_flows",
        sa.Column("digest", sa.String(64), primary_key=True),
        sa.Column("browser_digest", sa.String(64), nullable=False),
        sa.Column("verifier", sa.String(128), nullable=False),
        sa.Column("nonce", sa.String(128), nullable=False),
        sa.Column("return_to", sa.String(512), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_google_login_flows_expires_at", "google_login_flows", ["expires_at"])
    with op.batch_alter_table("accounts") as batch:
        batch.add_column(
            sa.Column("workspace_id", sa.String(36), nullable=False, server_default=OWNER)
        )
        batch.create_foreign_key("fk_accounts_workspace", "workspaces", ["workspace_id"], ["id"])
        batch.create_index("ix_accounts_workspace_id", ["workspace_id"])
    op.execute(f"UPDATE accounts SET workspace_id = '{SYSTEM}' WHERE mode = 'mock'")
    if op.get_bind().dialect.name == "postgresql":
        install_postgres_policies()


def downgrade():
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TRIGGER workspace_queue_limit ON jobs")
        op.execute("DROP FUNCTION limit_workspace_queue()")
        for table in SCOPED:
            op.execute(f"DROP POLICY workspace_access ON {table}")
            op.execute(f"DROP POLICY worker_access ON {table}")
            op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
        for role, tables in ((TENANT, (*SCOPED, *PUBLIC_LOOKUPS)), (WORKER, WORKER_TABLES)):
            op.execute(f"REVOKE ALL PRIVILEGES ON {', '.join(tables)} FROM {role}")
    with op.batch_alter_table("accounts") as batch:
        batch.drop_index("ix_accounts_workspace_id")
        batch.drop_constraint("fk_accounts_workspace", type_="foreignkey")
        batch.drop_column("workspace_id")
    for table in reversed(IDENTITY_TABLES):
        op.drop_table(table)
