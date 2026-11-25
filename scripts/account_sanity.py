"""Worker-local account comparison. No Instagram calls or raw session output.

Get-Content scripts/account_sanity.py -Raw | docker compose --env-file .env.readonly \
    -f compose.yaml -f compose.instagram.yaml exec -T worker python -
"""

import json
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.guarded_client import verify_installation
from app.config import settings
from app.db import engine
from app.models import (
    Account,
    AccountConnection,
    AccountSession,
    AccountSettings,
    ActionAttempt,
    BudgetUsage,
    Job,
    now,
)
from app.session_store import SessionStore


def session_summary(values):
    """Explicit allowlist: no IDs, cookies, headers, tokens or contexts."""
    profile = values.get("device_settings") or {}
    authorization = values.get("authorization_data") or {}
    cookies = values.get("cookies") or {}
    return {
        "device_profile": {
            key: profile.get(key)
            for key in (
                "app_version",
                "version_code",
                "android_version",
                "android_release",
                "manufacturer",
                "model",
                "cpu",
                "dpi",
                "resolution",
            )
        },
        "locale": values.get("locale"),
        "timezone_name": values.get("timezone_name"),
        "has_authenticated_material": bool(
            authorization.get("ds_user_id") or cookies.get("sessionid")
        ),
        "has_device_identifiers": all(
            bool((values.get("uuids") or {}).get(key))
            for key in (
                "uuid",
                "phone_id",
                "android_device_id",
            )
        ),
    }


def collect():
    config = settings()
    store = SessionStore(config.session_store_path, config.session_key_file)
    report = {
        "observed_at": now().isoformat(),
        "transport": config.adapter_mode,
        "pin": verify_installation(),
        "instagram_requests_sent_by_report": 0,
        "accounts": [],
        "comparisons": [],
    }
    private_devices = []
    with Session(engine) as db:
        for account in db.scalars(
            select(Account).where(Account.mode == "live").order_by(Account.created_at)
        ):
            metadata = db.get(AccountSession, account.id)
            preferences = db.get(AccountSettings, account.id)
            values = None
            if metadata:
                values = store.load(account.id, metadata.pending_revision or metadata.revision)
                private_devices.append(
                    (account.username, values.get("uuids") or {}, values.get("user_agent"))
                )
            connections = []
            for connection in db.scalars(
                select(AccountConnection)
                .where(AccountConnection.account_id == account.id)
                .order_by(AccountConnection.created_at)
            ):
                attempt = (
                    db.scalar(
                        select(ActionAttempt).where(ActionAttempt.job_id == connection.job_id)
                    )
                    if connection.job_id
                    else None
                )
                connections.append(
                    {
                        "created_at": connection.created_at,
                        "state": connection.state,
                        "error_code": connection.error_code,
                        "verification_branch": connection.challenge_kind,
                        "code_submitted": connection.code_submitted,
                        "requests": attempt.request_count if attempt else 0,
                        "duration_seconds": round(
                            (attempt.completed_at - attempt.started_at).total_seconds(), 1
                        )
                        if attempt and attempt.completed_at
                        else None,
                    }
                )
            report["accounts"].append(
                {
                    "username": account.username,
                    "account_id": account.id,
                    "session_state": account.session_state,
                    "local_restricted_flag": account.restricted,
                    "paused": account.paused,
                    "acquisition_enabled": account.live_acquisition_enabled,
                    "cleanup_enabled": account.live_cleanup_enabled,
                    "identity_verified": bool(account.instagram_id),
                    "session_last_validated_at": metadata.last_validated_at if metadata else None,
                    "session_decrypted": values is not None,
                    "session": session_summary(values) if values is not None else None,
                    "preferences": {
                        key: (preferences.values or {}).get(key)
                        for key in (
                            "request_ceiling",
                            "follows_per_day",
                            "unfollows_per_day",
                            "collection_batch_size",
                            "timezone",
                        )
                    }
                    if preferences
                    else None,
                    "requests_by_day": [
                        {"day": row.local_day, "consumed": row.consumed, "reserved": row.reserved}
                        for row in db.scalars(
                            select(BudgetUsage)
                            .where(
                                BudgetUsage.account_id == account.id,
                                BudgetUsage.category == "requests",
                            )
                            .order_by(BudgetUsage.local_day)
                        )
                    ],
                    "running_jobs": len(
                        list(
                            db.scalars(
                                select(Job.id).where(
                                    Job.account_id == account.id, Job.state == "running"
                                )
                            )
                        )
                    ),
                    "connections": connections,
                }
            )
    for index, (left, left_ids, left_agent) in enumerate(private_devices):
        for right, right_ids, right_agent in private_devices[index + 1 :]:
            report["comparisons"].append(
                {
                    "accounts": [left, right],
                    "same_user_agent": bool(
                        left_agent and right_agent and left_agent == right_agent
                    ),
                    "device_identifiers_distinct": {
                        key: bool(
                            left_ids.get(key)
                            and right_ids.get(key)
                            and left_ids[key] != right_ids[key]
                        )
                        for key in ("uuid", "phone_id", "android_device_id")
                    },
                }
            )
    return report


if __name__ == "__main__":
    try:
        print(
            json.dumps(
                collect(),
                default=lambda value: (
                    value.isoformat() if isinstance(value, datetime) else str(value)
                ),
                indent=2,
            )
        )
    except Exception:
        # Do not render exceptions from session decoding or private config.
        raise SystemExit(
            "Account sanity comparison failed; no session material was displayed."
        ) from None
