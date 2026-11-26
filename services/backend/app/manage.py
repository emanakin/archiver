"""Owner-operated backend setup/recovery. Never invoked by API request handlers."""

import argparse
import json
import os
import uuid
from datetime import timedelta
from getpass import getpass
from pathlib import Path

from cryptography.fernet import Fernet
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from . import budgets
from .account_lane import AccountLane
from .adapters.guarded_client import build_client, translate, verify_installation
from .config import settings
from .db import engine
from .execution_errors import ExecutionError, OperationBlocked, OwnershipLost, SessionRequired
from .executor import Executor, RequestGuard
from .models import (
    Account,
    AccountSession,
    AccountSettings,
    ActionAttempt,
    AttemptEvent,
    AuditEvent,
    Job,
    now,
)
from .schemas import SettingsValues, SourceCreate
from .session_store import SessionStore


def create_account(username, request_budget):
    normalized = SourceCreate(
        account_id="local-setup", kind="profile_followers", value=username
    ).value
    with Session(engine) as db:
        if db.scalar(select(Account).where(Account.username == normalized, Account.mode == "live")):
            raise OperationBlocked("A live account with that handle is already provisioned")
        account = Account(username=normalized, mode="live", paused=True)
        db.add(account)
        db.flush()
        db.add(
            AccountSettings(
                account_id=account.id,
                values=SettingsValues(request_ceiling=request_budget).model_dump(),
            )
        )
        db.add(
            AuditEvent(account_id=account.id, event="account_provisioned", detail={"mode": "live"})
        )
        account_id = account.id
        db.commit()
    print("Provisioned paused account:", account_id)
    return account_id


def connect_account(
    account_id,
    password=None,
    verification_code="",
    recover=False,
    verify=False,
    max_requests=10,
    manual_profile_verification=False,
    profile_code_provider=None,
    mfa_code_provider=None,
    connection_check=None,
    on_started=None,
    on_verified=None,
    verification_waiter=None,
):
    if manual_profile_verification and ((not recover and not verification_waiter) or verify):
        raise OperationBlocked("Manual profile verification requires explicit password recovery")
    if settings().adapter_mode == "mock":
        raise OperationBlocked("Configure the reviewed read-only worker before connecting")
    pin = verify_installation()
    store = SessionStore(settings().session_store_path, settings().session_key_file)
    with AccountLane(engine, account_id) as lane:
        if not lane.acquired:
            raise OperationBlocked("Account request lane is busy")
        with Session(engine) as db:
            account = db.get(Account, account_id)
            if account is None or account.mode != "live":
                raise OperationBlocked("Provision a separate live account before connecting")
            if db.scalar(
                select(Job.id).where(Job.account_id == account_id, Job.state == "running").limit(1)
            ):
                raise OperationBlocked(
                    "An earlier account operation must finish or recover its lease before connecting"
                )
            if account.restricted and not recover:
                raise OperationBlocked(
                    "Review the account in the official app and use recover with acknowledgement"
                )
            metadata = db.get(AccountSession, account_id)
            values = (
                store.load(account_id, metadata.pending_revision or metadata.revision)
                if metadata
                else {}
            )
            if verify and metadata is None:
                raise SessionRequired("No saved session exists")
            if not verify:
                # Reuse device context but never let login() automatically relogin
                # with a rejected cookie. Credentials are supplied only this time.
                values = {**values, "cookies": {}, "authorization_data": {}}
            username = account.username
        with Session(engine) as db, db.begin():
            db.scalar(select(Account).where(Account.id == account_id).with_for_update())
            job = Job(
                account_id=account_id,
                kind="verify" if verify else "connect",
                state="running",
                idempotency_key="owner-connection:" + str(uuid.uuid4()),
                payload={
                    "recovery_acknowledged": recover,
                    "manual_profile_verification": manual_profile_verification,
                    "interactive_mfa": not verify and not verification_code,
                },
                claim_owner=lane.owner,
                started_at=now(),
                heartbeat_at=now(),
                lease_expires_at=now() + timedelta(seconds=90),
                attempt_count=1,
            )
            db.add(job)
            db.flush()
            attempt = ActionAttempt(account_id=account_id, job_id=job.id, owner=lane.owner)
            db.add(attempt)
            db.flush()
            db.add(
                AttemptEvent(
                    attempt_id=attempt.id,
                    event="owner_connection_started",
                    detail={"verify_only": verify},
                )
            )
            job_id, attempt_id = job.id, attempt.id
        guard = RequestGuard(engine, lane, job_id, attempt_id)
        guard.connection_check = connection_check
        guard.allow_auth = not verify
        guard.manual_profile_verification = manual_profile_verification

        def wait_for_owner(kind, prompt, provider):
            guard.owner_code_checkpoint()
            if verification_waiter:
                value = verification_waiter(
                    kind, lambda: guard.owner_code_checkpoint(heartbeat_only=True)
                )
            else:
                value = (provider or getpass)(prompt)
            guard.owner_code_checkpoint(received=True)
            return value

        if not verify and not verification_code:

            def provide_mfa_code():
                return wait_for_owner(
                    "mfa",
                    "Instagram requested MFA. Enter its new 6-digit code (Enter to cancel): ",
                    mfa_code_provider,
                )

            guard.mfa_code_provider = provide_mfa_code
        if manual_profile_verification:

            def provide_profile_code():
                return wait_for_owner(
                    "profile",
                    "Profile verification code requested by Instagram (Enter to cancel): ",
                    profile_code_provider,
                )

            guard.profile_code_provider = provide_profile_code
            if verification_waiter:
                guard.profile_approval_provider = lambda: wait_for_owner("approval", "", None)
        client = None
        try:
            if on_started:
                on_started(job_id)
            with Session(engine) as db, db.begin():
                db.scalar(select(Account).where(Account.id == account_id).with_for_update())
                budgets.reserve(
                    db, account_id, attempt_id, "requests", 1 if verify else max_requests, now()
                )
            client = build_client(guard, values)
            if not verify:
                client.login(username, password, verification_code=verification_code)
            identity = client.account_info()
            instagram_id = str(identity.pk)
            if not instagram_id.isdecimal():
                raise SessionRequired("Instagram returned an invalid account identity")
            with Session(engine) as db:
                existing_id = db.get(Account, account_id).instagram_id
            if existing_id and existing_id != instagram_id:
                raise SessionRequired("Connected identity does not match the provisioned account")
            with Session(engine) as db, db.begin():
                account = db.scalar(
                    select(Account).where(Account.id == account_id).with_for_update()
                )
                if engine.dialect.name == "postgresql":
                    db.execute(
                        text("SELECT pg_advisory_xact_lock(hashtextextended(:identity, 0))"),
                        {"identity": f"archive-instagram:{instagram_id}"},
                    )
                if db.scalar(
                    select(Account.id).where(
                        Account.instagram_id == instagram_id, Account.id != account.id
                    )
                ):
                    raise OperationBlocked("This Instagram account is already connected")
                if on_verified:
                    on_verified(db)
                revision = store.save(account_id, client.get_settings())
                metadata = db.get(AccountSession, account_id)
                if metadata is None:
                    metadata = AccountSession(
                        account_id=account_id, revision=revision, library_version=pin["version"]
                    )
                    db.add(metadata)
                metadata.revision, metadata.pending_revision = revision, None
                metadata.last_validated_at = account.read_only_verified_at = now()
                metadata.capabilities = {**(metadata.capabilities or {}), "account_verified": True}
                account.instagram_id, account.username = instagram_id, identity.username
                account.session_state = "connected"
                if not verify:
                    account.paused = True
                    account.live_acquisition_enabled = account.live_cleanup_enabled = False
                if recover:
                    account.restricted, account.cooldown_until, account.automatic_pause_reason = (
                        False,
                        None,
                        None,
                    )
                elif account.automatic_pause_reason == "SESSION_REQUIRED":
                    account.automatic_pause_reason = None
                for blocked in db.scalars(
                    select(Job).where(
                        Job.account_id == account_id,
                        Job.state == "blocked",
                        Job.kind.not_in(["connect", "verify"]),
                        Job.dispatch_started_at.is_(None),
                        Job.error.in_(["SESSION_REQUIRED", "ACCOUNT_RESTRICTED"]),
                    )
                ):
                    blocked.state, blocked.error, blocked.completed_at = "queued", None, None
                db.add(
                    AuditEvent(
                        account_id=account_id,
                        event="session_verified",
                        detail={"library_version": pin["version"]},
                    )
                )
            Executor(engine, None).finish(lane, job_id, attempt_id, "succeeded")
            print("Session verified. Pause and live action enablement remain under owner control.")
        except OwnershipLost:
            raise
        except Exception as exc:
            failure = translate(exc)
            if client is not None:
                revision = store.save(account_id, client.get_settings())
                with Session(engine) as db, db.begin():
                    db.scalar(select(Account).where(Account.id == account_id).with_for_update())
                    metadata = db.get(AccountSession, account_id)
                    if metadata:
                        metadata.pending_revision = revision
                    else:
                        db.add(
                            AccountSession(
                                account_id=account_id,
                                revision=revision,
                                library_version=pin["version"],
                            )
                        )
            Executor(engine, None).finish(
                lane, job_id, attempt_id, "blocked", failure.code, failure
            )
            raise failure from None
        finally:
            password = verification_code = None
            if client is not None:
                client.password = None


def main():
    parser = argparse.ArgumentParser(
        description="Archive Desk owner setup; secrets are prompted, never accepted as arguments"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser(
        "capabilities", help="Offline library pin/signature check; no Instagram requests"
    )
    key = commands.add_parser(
        "init-session-key", help="Create a new key without overwriting an existing file"
    )
    key.add_argument("--path", default=settings().session_key_file)
    create = commands.add_parser("account-create")
    create.add_argument("--username", required=True)
    create.add_argument("--request-budget", type=int, required=True)
    for name in ("connect", "recover", "verify"):
        action = commands.add_parser(name)
        action.add_argument("--account-id", required=True)
        action.add_argument("--max-requests", type=int, default=10)
        if name == "recover":
            action.add_argument("--acknowledge-recovery", action="store_true", required=True)
            action.add_argument("--manual-profile-verification", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "init-session-key":
            path = Path(args.path)
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as file:
                file.write(Fernet.generate_key())
            print("Created session key. Back it up separately from encrypted session files.")
        elif args.command == "capabilities":
            import inspect

            from instagrapi import Client

            pin = verify_installation()
            required = {
                "user_followers_v1_chunk": ("user_id", "max_amount", "max_id", "order"),
                "media_likers": ("media_id",),
                "user_follow": ("user_id",),
                "user_unfollow": ("user_id",),
                "account_info": (),
                "login": ("username", "password", "verification_code"),
                "bloks_ap_two_step_verification_entrypoint": ("context_data",),
                "bloks_ap_two_step_verification_code_entry": ("context_data",),
                "bloks_ap_two_step_verification_submit_code": ("context_data", "code"),
                "bloks_caa_login": ("verification_code",),
                "bloks_two_step_verification_entrypoint": ("two_step_verification_context",),
                "bloks_two_step_verification_method_picker": ("two_step_verification_context",),
                "bloks_two_step_verification_select_method": (
                    "two_step_verification_context",
                    "selected_method",
                ),
                "bloks_two_step_verification_verify_code": (
                    "two_step_verification_context",
                    "code",
                    "challenge",
                ),
            }
            for method, names in required.items():
                signature = inspect.signature(getattr(Client, method))
                if not all(name in signature.parameters for name in names):
                    raise OperationBlocked(
                        "Installed library signature differs from the adapter contract"
                    )
            print(
                json.dumps({**pin, "signatures_verified": sorted(required), "network_requests": 0})
            )
        elif args.command == "account-create":
            create_account(args.username, args.request_budget)
        else:
            if not 1 <= args.max_requests <= 50:
                raise OperationBlocked("max-requests must be between 1 and 50")
            password = (
                getpass("Instagram password (used only for this operation): ")
                if args.command != "verify"
                else None
            )
            connect_account(
                args.account_id,
                password=password,
                recover=args.command == "recover",
                verify=args.command == "verify",
                max_requests=args.max_requests,
                manual_profile_verification=getattr(args, "manual_profile_verification", False),
            )
    except (ExecutionError, OSError, ValueError) as exc:
        code = exc.code if isinstance(exc, ExecutionError) else "SETUP_FAILED"
        raise SystemExit(
            f"{code}: setup did not complete. Review configuration and recovery instructions; no automatic retry."
        ) from None


if __name__ == "__main__":
    main()
