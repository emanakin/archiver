import os
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.exc import IntegrityError, ProgrammingError
from sqlalchemy.orm import Session, sessionmaker

from app import db as database
from app import google_signin
from app.config import settings
from app.main import app
from app.models import (
    OWNER_WORKSPACE_ID,
    SYSTEM_WORKSPACE_ID,
    Account,
    ContentAsset,
    ContentDraft,
    Job,
)

pytestmark = pytest.mark.skipif(not os.getenv("TEST_POSTGRES_URL"), reason="Needs PostgreSQL")


def restricted_engine(source, role):
    engine = create_engine(source.url)
    with source.connect() as conn:
        schema = conn.scalar(text("select current_schema()"))
    assert schema.startswith("test_") and schema[5:].isalnum()
    assert role in {"archive_tenant_request", "archive_identity_request", "archive_worker_data"}

    @event.listens_for(engine, "connect")
    def set_scope(conn, _record):
        conn.autocommit = True
        conn.execute(f'SET search_path TO "{schema}"')
        conn.execute(f"SET SESSION AUTHORIZATION {role}")
        conn.autocommit = False

    return engine


def test_google_workspaces_and_postgres_isolation(postgres, monkeypatch):
    admin, mock_account_id = postgres
    cfg = settings()
    monkeypatch.setattr(cfg, "auth_mode", "google")
    monkeypatch.setattr(cfg, "app_origin", "https://archive.test")
    monkeypatch.setattr(cfg, "bootstrap_owner_email", "owner@example.test")
    monkeypatch.setattr(cfg, "signup_test_emails", "second@example.test")
    monkeypatch.setattr(cfg, "signup_open", False)
    api = restricted_engine(admin, "archive_tenant_request")
    identity = restricted_engine(admin, "archive_identity_request")
    worker = restricted_engine(admin, "archive_worker_data")
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=api, expire_on_commit=False))
    monkeypatch.setattr(google_signin, "identity_db", lambda: Session(identity))
    monkeypatch.setattr(
        google_signin,
        "oauth_client",
        lambda _path: ("synthetic.apps.googleusercontent.com", "test"),
    )
    exchanges = []

    def exchange(code, verifier, nonce):
        exchanges.append(code)
        assert len(verifier) == len(nonce) == 43
        subject, email = code.split(",", 1)
        return subject, email

    monkeypatch.setattr(google_signin, "post_google", exchange)

    def client():
        return TestClient(
            app,
            base_url=cfg.app_origin,
            headers={"X-Internal-Token": cfg.internal_api_token, "Origin": cfg.app_origin},
            follow_redirects=False,
        )

    def start(browser):
        response = browser.get("/auth/google/start", params={"return_to": "/content?tab=drafts"})
        assert response.status_code == 303
        query = parse_qs(urlsplit(response.headers["location"]).query)
        assert query["code_challenge_method"] == ["S256"]
        return query["state"][0]

    def callback(browser, state, code):
        return browser.get("/auth/google/callback", params={"state": state, "code": code})

    try:
        with Session(admin) as session, session.begin():
            session.get(Account, mock_account_id).workspace_id = SYSTEM_WORKSPACE_ID
            account = Account(
                username="owner_archive", mode="live", workspace_id=OWNER_WORKSPACE_ID
            )
            session.add(account)
            session.flush()
            owner_id = account.id
            asset = ContentAsset(
                account_id=owner_id,
                sha256="a" * 64,
                mime="image/jpeg",
                byte_size=100,
                width=100,
                height=100,
            )
            session.add(asset)
            session.flush()
            draft = ContentDraft(account_id=owner_id, asset_id=asset.id, caption="Owner draft")
            session.add(draft)
            session.flush()
            draft_id = draft.id
            session.add(Job(account_id=account.id, kind="test", idempotency_key="owner-only"))
        with client() as owner, client() as second, client() as stranger:
            assert stranger.get("/api/v1/me").status_code == 401
            owner_state = start(owner)
            blocked = callback(stranger, owner_state, "owner-sub,owner@example.test")
            assert blocked.headers["location"] == "/login?error=google"
            assert exchanges == []
            auth = callback(owner, owner_state, "owner-sub,owner@example.test")
            assert auth.headers["location"] == "/content?tab=drafts"
            assert "HttpOnly" in str(auth.headers.get_list("set-cookie"))
            assert "Secure" in str(auth.headers.get_list("set-cookie"))
            assert (
                callback(owner, owner_state, "owner-sub,owner@example.test").headers["location"]
                == "/login?error=google"
            )
            assert owner.get("/api/v1/me").json()["workspace_id"] == OWNER_WORKSPACE_ID
            assert [row["id"] for row in owner.get("/api/v1/accounts").json()["items"]] == [
                owner_id
            ]
            callback(second, start(second), "second-sub,second@example.test")
            other = second.get("/api/v1/me").json()["workspace_id"]
            assert other != OWNER_WORKSPACE_ID
            assert second.get("/api/v1/accounts").json()["items"] == []
            assert second.get(f"/api/v1/accounts/{owner_id}/settings").status_code == 404
            assert second.get("/api/v1/jobs", params={"account_id": owner_id}).status_code == 404
            assert (
                second.get(
                    f"/api/v1/content-drafts/{draft_id}/preview", params={"revision": 1}
                ).status_code
                == 404
            )
            assert (
                second.post(
                    f"/api/v1/content-drafts/{draft_id}/publish",
                    json={"expected_revision": 1, "reviewed": True},
                ).status_code
                == 404
            )
            assert (
                callback(stranger, start(stranger), "new-sub,new@example.test").headers["location"]
                == "/login?error=signup_closed"
            )
            assert (
                callback(stranger, start(stranger), "forged-sub,owner@example.test").headers[
                    "location"
                ]
                == "/login?error=signup_closed"
            )
            assert (
                owner.post("/auth/logout", headers={"Origin": "https://other.test"}).status_code
                == 403
            )
            assert owner.post("/auth/logout").status_code == 204
            assert owner.get("/api/v1/me").status_code == 401
            monkeypatch.setattr(cfg, "signup_open", True)
            assert (
                callback(stranger, start(stranger), "new-sub,new@example.test").status_code == 303
            )
            assert stranger.get("/api/v1/me").json()["workspace_id"] not in {
                OWNER_WORKSPACE_ID,
                other,
            }
            assert stranger.get("/api/v1/accounts").json()["items"] == []
            with Session(api, info={"workspace_id": other}) as session:
                assert session.scalars(select(Job)).all() == []
                second_account = Account(username="other_archive", mode="live", workspace_id=other)
                session.add(second_account)
                session.flush()
                second_id = second_account.id
                session.add_all(
                    [
                        Job(account_id=second_id, kind="test", idempotency_key=str(i))
                        for i in range(30)
                    ]
                )
                session.commit()
                assert len(session.scalars(select(Job)).all()) == 30
                session.add(Job(account_id=second_id, kind="test", idempotency_key="over-quota"))
                with pytest.raises(IntegrityError, match="Workspace queue is full"):
                    session.flush()
                session.rollback()
            with Session(api) as session:
                assert session.scalars(select(Account)).all() == []
                with pytest.raises(ProgrammingError):
                    session.execute(text("SELECT * FROM users"))
            with Session(identity) as session, pytest.raises(ProgrammingError):
                session.execute(text("SELECT * FROM accounts"))
            with Session(worker) as session:
                assert len(session.scalars(select(Job)).all()) == 31
    finally:
        api.dispose()
        identity.dispose()
        worker.dispose()
