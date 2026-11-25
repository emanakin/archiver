"""Owner browser onboarding: encrypted handoff only, never Instagram HTTP here."""

import hashlib
import uuid
from datetime import datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field, SecretStr, field_validator
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from .config import settings
from .dashboard import account_settings
from .db import get_db
from .executor import aware
from .models import (
    Account,
    AccountConnection,
    AccountSettings,
    ActionAttempt,
    AttemptEvent,
    AuditEvent,
    Job,
    OnboardingKey,
    now,
)
from .onboarding_crypto import seal
from .schemas import ORMResponse, SettingsValues, SourceCreate, Strict

router = APIRouter(prefix="/api/v1")
ACTIVE = {"queued", "connecting", "awaiting_code", "awaiting_approval", "verifying"}


class ConnectInput(Strict):
    username: str = Field(min_length=1, max_length=64)
    password: SecretStr = Field(min_length=1, max_length=1024)
    idempotency_key: str = Field(min_length=8, max_length=200)
    request_budget: int = Field(default=20, ge=12, le=10000)
    recovery_acknowledged: bool = False

    @field_validator("username")
    @classmethod
    def normalize(cls, value):
        return SourceCreate(account_id="onboarding", kind="profile_followers", value=value).value


class ApprovalInput(Strict):
    challenge_id: str
    choice: Literal["approved", "code"] = "approved"


class CodeInput(Strict):
    code: SecretStr
    challenge_id: str

    @field_validator("code")
    @classmethod
    def valid_code(cls, value):
        import re

        if not re.fullmatch(r"[0-9]{6,8}", value.get_secret_value()):
            raise ValueError("Enter the six-to-eight-digit verification code")
        return value


class ConnectionProgress(ORMResponse):
    id: str
    account_id: str
    state: Literal[
        "queued",
        "connecting",
        "awaiting_code",
        "awaiting_approval",
        "verifying",
        "connected",
        "failed",
        "cancelled",
        "expired",
    ]
    challenge_id: str | None
    challenge_kind: str | None
    error_code: str | None
    expires_at: datetime
    message: str


class OnboardingCapability(Strict):
    available: bool
    transport_mode: str
    reason: str | None


def current_key(db):
    key = db.scalar(select(OnboardingKey).order_by(OnboardingKey.observed_at.desc()).limit(1))
    return key if key and aware(key.observed_at) > now() - timedelta(seconds=60) else None


def clear_secrets(connection):
    connection.password_envelope = connection.code_envelope = None


def expire(connection):
    if connection.state in ACTIVE and aware(connection.expires_at) <= now():
        connection.state, connection.error_code = "expired", "CONNECTION_EXPIRED"
        clear_secrets(connection)


def progress(connection):
    messages = {
        "queued": "Your account is waiting for the connection worker.",
        "connecting": "Contacting Instagram. Verification will appear here if requested.",
        "awaiting_code": "Instagram requested verification. Enter the code below.",
        "awaiting_approval": "Check Instagram on your phone and approve the login if prompted. Then continue here. This connection stays open while you review it.",
        "verifying": "Checking your verification code with Instagram.",
        "connected": "Instagram connected. Automations remain paused.",
        "cancelled": "Connection cancelled. You can start again when ready.",
        "expired": "This connection expired. Start a new connection when ready.",
        "failed": "Instagram connection did not complete. Review the account in Instagram before trying again.",
    }
    if connection.state == "verifying" and connection.challenge_kind == "approval":
        message = "Checking the verification screen after your Instagram approval. Your password is not being sent again."
    elif connection.state == "verifying" and connection.challenge_kind == "approval_code":
        message = (
            "Asking Instagram for its code-entry screen. Your password is not being sent again."
        )
    elif (
        connection.error_code == "PROFILE_CONTINUATION_UNAVAILABLE"
        and connection.challenge_kind == "approval_code"
    ):
        message = "Instagram did not return a code-entry screen after you reported a code prompt. Open the connection log to see which screens Instagram returned. Account actions remain paused."
    elif connection.error_code == "PROFILE_CONTINUATION_UNAVAILABLE":
        message = "The app could not confirm a signed-in session after you acknowledged phone approval. This does not show that phone verification failed or the account was blocked. Account actions remain paused."
    elif connection.error_code == "PROFILE_VERIFICATION_REQUIRED":
        message = "Archive Desk could not complete this verification screen, so the app connection remains incomplete. This result does not establish an incorrect code or an Instagram account restriction."
    elif connection.error_code == "BUDGET_EXHAUSTED":
        message = "Your daily request allowance is exhausted or too small for this connection. Review it in Settings or wait until it resets."
    elif connection.error_code == "WORKER_RESTARTED":
        message = "The connection worker restarted. Your password and code were cleared; reconnect to continue."
    else:
        message = messages[connection.state]
    return {
        name: getattr(connection, name)
        for name in (
            "id",
            "account_id",
            "state",
            "challenge_id",
            "challenge_kind",
            "error_code",
            "expires_at",
        )
    } | {"message": message}


@router.get("/onboarding-capability", response_model=OnboardingCapability)
def capability(db: Session = Depends(get_db)):
    available = settings().adapter_mode != "mock" and current_key(db) is not None
    return {
        "available": available,
        "transport_mode": settings().adapter_mode,
        "reason": None
        if available
        else "Account connection is temporarily unavailable while the Instagram worker is offline. Your saved data is unchanged.",
    }


@router.post("/account-connections", status_code=202, response_model=ConnectionProgress)
def begin_connection(body: ConnectInput, db: Session = Depends(get_db)):
    key = current_key(db)
    if settings().adapter_mode == "mock" or key is None:
        raise HTTPException(503, "The Instagram connection worker is not ready. Try again shortly.")
    if db.bind.dialect.name == "postgresql":
        lock = int.from_bytes(
            hashlib.sha256(("onboarding:" + body.username).encode()).digest()[:8],
            "big",
            signed=True,
        )
        db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock})
    previous = db.scalar(
        select(AccountConnection).where(AccountConnection.idempotency_key == body.idempotency_key)
    )
    if previous:
        if db.get(Account, previous.account_id).username != body.username:
            raise HTTPException(409, "This connection request was already used for another account")
        return progress(previous)
    account = db.scalar(
        select(Account)
        .where(func.lower(Account.username) == body.username, Account.mode == "live")
        .with_for_update()
    )
    if account is None:
        account = Account(username=body.username, mode="live", paused=True)
        db.add(account)
        db.flush()
        db.add(
            AccountSettings(
                account_id=account.id,
                values=SettingsValues(
                    request_ceiling=body.request_budget, collection_batch_size=5
                ).model_dump(),
            )
        )
    if account.restricted and not body.recovery_acknowledged:
        raise HTTPException(
            409, "Review the account in Instagram and acknowledge recovery before reconnecting"
        )
    for active in db.scalars(
        select(AccountConnection)
        .where(AccountConnection.account_id == account.id, AccountConnection.state.in_(ACTIVE))
        .with_for_update()
    ):
        expire(active)
        if active.state in ACTIVE:
            raise HTTPException(409, "A connection is already in progress for this account")
    if db.scalar(
        select(Job.id).where(Job.account_id == account.id, Job.state == "running").limit(1)
    ):
        raise HTTPException(409, "An account operation is still running. Wait for it to finish.")
    config = account_settings(db, account.id)
    ceiling = config["request_ceiling"]
    connection = AccountConnection(
        id=str(uuid.uuid4()),
        account_id=account.id,
        key_id=key.id,
        idempotency_key=body.idempotency_key,
        state="queued",
        recovery_acknowledged=body.recovery_acknowledged,
        max_requests=12 if ceiling is None else min(12, ceiling),
        expires_at=now() + timedelta(minutes=10),
    )
    if connection.max_requests < 1:
        raise HTTPException(
            409, "Set a positive daily request allowance in Settings before connecting"
        )
    connection.password_envelope = seal(
        key.public_key, body.password.get_secret_value(), f"{connection.id}:{account.id}:password"
    )
    account.paused = True
    account.live_acquisition_enabled = account.live_cleanup_enabled = False
    db.add(connection)
    db.add(
        AuditEvent(
            account_id=account.id,
            event="browser_connection_requested",
            detail={"connection_id": connection.id},
        )
    )
    db.commit()
    return progress(connection)


@router.get("/accounts/{account_id}/onboarding", response_model=ConnectionProgress | None)
def latest_connection(account_id: str, db: Session = Depends(get_db)):
    account = db.get(Account, account_id)
    if not account or account.mode != "live":
        raise HTTPException(404, "Account not found")
    connection = db.scalar(
        select(AccountConnection)
        .where(AccountConnection.account_id == account_id)
        .order_by(AccountConnection.created_at.desc())
        .with_for_update()
        .limit(1)
    )
    if connection:
        expire(connection)
        db.commit()
        return progress(connection)
    return None


@router.post("/account-connections/{connection_id}/code", response_model=ConnectionProgress)
def submit_code(connection_id: str, body: CodeInput, db: Session = Depends(get_db)):
    connection = db.scalar(
        select(AccountConnection).where(AccountConnection.id == connection_id).with_for_update()
    )
    if not connection:
        raise HTTPException(404, "Connection not found")
    expire(connection)
    if (
        connection.state != "awaiting_code"
        or connection.code_submitted
        or connection.challenge_id != body.challenge_id
    ):
        db.commit()
        raise HTTPException(409, "This verification step is no longer waiting for a code")
    key = db.get(OnboardingKey, connection.key_id)
    if not key or aware(key.observed_at) < now() - timedelta(seconds=60):
        raise HTTPException(
            503, "The connection worker is offline. Reconnect when it is available."
        )
    connection.code_envelope = seal(
        key.public_key,
        body.code.get_secret_value(),
        f"{connection.id}:{connection.account_id}:code",
    )
    connection.code_submitted, connection.state = True, "verifying"
    db.commit()
    return progress(connection)


@router.post("/account-connections/{connection_id}/cancel", response_model=ConnectionProgress)
def cancel_connection(connection_id: str, db: Session = Depends(get_db)):
    connection = db.scalar(
        select(AccountConnection).where(AccountConnection.id == connection_id).with_for_update()
    )
    if not connection:
        raise HTTPException(404, "Connection not found")
    if connection.state in ACTIVE:
        connection.state = "cancelled"
        clear_secrets(connection)
    db.commit()
    return progress(connection)


@router.post("/account-connections/{connection_id}/approval", response_model=ConnectionProgress)
def confirm_approval(connection_id: str, body: ApprovalInput, db: Session = Depends(get_db)):
    connection = db.scalar(
        select(AccountConnection).where(AccountConnection.id == connection_id).with_for_update()
    )
    if not connection:
        raise HTTPException(404, "Connection not found")
    expire(connection)
    if connection.state != "awaiting_approval" or connection.challenge_id != body.challenge_id:
        db.commit()
        raise HTTPException(409, "This connection is no longer waiting for phone approval")
    connection.state = "verifying"
    if body.choice == "code":
        connection.challenge_kind = "approval_code"
    db.commit()
    return progress(connection)


class ConnectionLogEvent(Strict):
    at: datetime
    event: str
    detail: dict


class ConnectionLog(Strict):
    connection_id: str
    state: str
    error_code: str | None
    request_count: int
    events: list[ConnectionLogEvent]


# Only journal keys written by the executor/guard. Anything else is withheld.
LOG_DETAIL_KEYS = {"ordinal", "mutation", "stage", "screens", "session", "choice", "state", "code"}


@router.get("/account-connections/{connection_id}/log", response_model=ConnectionLog)
def connection_log(connection_id: str, db: Session = Depends(get_db)):
    connection = db.get(AccountConnection, connection_id)
    if not connection:
        raise HTTPException(404, "Connection not found")
    events, count = [], 0
    if connection.job_id:
        attempts = db.scalars(
            select(ActionAttempt)
            .where(ActionAttempt.job_id == connection.job_id)
            .order_by(ActionAttempt.started_at)
        ).all()
        count = sum(attempt.request_count for attempt in attempts)
        for event in db.scalars(
            select(AttemptEvent)
            .where(AttemptEvent.attempt_id.in_([attempt.id for attempt in attempts]))
            .order_by(AttemptEvent.created_at, AttemptEvent.id)
        ):
            detail = {
                key: value for key, value in (event.detail or {}).items() if key in LOG_DETAIL_KEYS
            }
            events.append({"at": event.created_at, "event": event.event, "detail": detail})
    return {
        "connection_id": connection.id,
        "state": connection.state,
        "error_code": connection.error_code,
        "request_count": count,
        "events": events,
    }
