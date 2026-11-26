from fastapi import HTTPException, Request
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from .config import settings

engine = create_engine(settings().database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


@event.listens_for(Session, "after_begin")
def apply_workspace(db, transaction, connection):
    workspace = db.info.get("workspace_id")
    if workspace:
        if connection.dialect.name != "postgresql":
            raise RuntimeError("Workspace requests require PostgreSQL")
        connection.execute(
            text("select set_config('archive.workspace_id', :workspace, true)"),
            {"workspace": workspace},
        )


def get_db(request: Request):
    workspace = None
    if settings().auth_mode == "google":
        principal = getattr(request.state, "principal", None)
        if not principal:
            raise HTTPException(401, "Sign in with Google")
        workspace = principal.workspace_id
    with SessionLocal(info={"workspace_id": workspace}) as db:
        yield db
