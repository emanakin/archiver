import os
import uuid
from pathlib import Path

os.environ["INTERNAL_API_TOKEN"] = "test-token-for-local-tests-only-123456"
os.environ["DATABASE_URL"] = "sqlite://"

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_db
from app.main import app
from app.models import Account, Base, Relationship, Target


@pytest.fixture
def system():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        account = Account(username="demo", paused=False)
        target = Target(id="demo-target", username="example")
        db.add_all([account, target])
        db.flush()
        rel = Relationship(account_id=account.id, target_id=target.id, observed_state="none")
        db.add(rel)
        db.commit()

        def override():
            yield db

        app.dependency_overrides[get_db] = override
        with TestClient(
            app,
            headers={
                "X-Internal-Token": os.environ["INTERNAL_API_TOKEN"],
                "Origin": "http://localhost:8080",
            },
        ) as client:
            yield client, db, account, rel
        app.dependency_overrides.clear()
    engine.dispose()


@pytest.fixture
def postgres():
    url = os.environ["TEST_POSTGRES_URL"]
    schema = "test_" + uuid.uuid4().hex
    admin = create_engine(url)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    try:
        config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
        config.set_main_option(
            "script_location", str(Path(__file__).resolve().parents[1] / "migrations")
        )
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
        with Session(engine) as db:
            account = Account(username="concurrency_demo", paused=False, mode="mock")
            db.add(account)
            db.add(Target(id="demo-concurrent", username="sample_concurrent"))
            db.flush()
            db.add(
                Relationship(
                    account_id=account.id, target_id="demo-concurrent", observed_state="none"
                )
            )
            db.commit()
            account_id = account.id
        yield engine, account_id
    finally:
        engine.dispose()
        # Schema is generated here, never a caller-supplied production identifier.
        assert schema.startswith("test_") and len(schema) == 37
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()
