"""One bounded owner-selected read suite through the installed API/worker.

Run in the API container via stdin with --account HANDLE --target HANDLE.
Requires a connected, paused account with no active work or schedules.
Live transport requires an explicit flag. Restores pause and cancels leftover reads.
"""

import argparse
import json
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from app.config import settings
from app.db import engine
from app.models import Account, AccountConnection, AuditEvent, InsightWatch, Job, Source
from app.onboarding_routes import ACTIVE
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--account", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument(
        "--kind",
        action="append",
        choices=(
            "profile",
            "relationship",
            "following",
            "followers",
            "posts",
            "stories",
            "likers",
        ),
    )
    parser.add_argument("--limit", type=int, default=3, choices=range(1, 6))
    parser.add_argument("--allow-live-transport", action="store_true")
    args = parser.parse_args()
    config = settings()
    if config.adapter_mode != "read_only" and not (
        config.adapter_mode == "live" and args.allow_live_transport
    ):
        raise SystemExit(
            "Read suite requires read_only transport or --allow-live-transport"
        )

    def api(path, body=None):
        request = Request(
            "http://127.0.0.1:8000/api/v1" + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={
                "X-Internal-Token": config.internal_api_token,
                "Origin": config.app_origin,
                "Content-Type": "application/json",
            },
        )
        try:
            with urlopen(request, timeout=15) as response:
                return json.load(response)
        except HTTPError as error:
            raise RuntimeError(
                f"Local API rejected read test with HTTP{error.code}"
            ) from None

    import uuid

    account = next(
        (
            item
            for item in api("/accounts?mode=live")["items"]
            if item["username"].lower() == args.account.lstrip("@").lower()
        ),
        None,
    )
    if (
        not account
        or account["session_state"] != "connected"
        or account["restricted"]
        or not account["paused"]
    ):
        raise SystemExit("Choose an already connected, unrestricted, paused account")
    if account["live_acquisition_enabled"] or account["live_cleanup_enabled"]:
        raise SystemExit("Disable action workflows before the read suite")
    account_id = account["id"]
    with Session(engine) as db:
        if db.scalar(
            select(Job.id)
            .where(Job.account_id == account_id, Job.state.in_(["queued", "running"]))
            .limit(1)
        ):
            raise SystemExit("Account queue is not idle; existing work preserved")
    before = api(f"/overview?account_id={account_id}")["requests_used"]
    checks = []
    results = []
    unpaused = False
    succeeded = False

    def enqueue(kind, target):
        check = api(
            "/insight-checks",
            {
                "account_id": account_id,
                "kind": kind,
                "target": target,
                "requested_limit": args.limit,
                "idempotency_key": "read-smoke:" + str(uuid.uuid4()),
            },
        )
        checks.append(check)
        return check

    def wait(check):
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            result = api(f"/insight-checks/{check['id']}")
            if result["state"] not in {"queued", "running"}:
                results.append(result)
                print(
                    json.dumps(
                        {
                            "kind": result["kind"],
                            "state": result["state"],
                            "requests": result["requests_used"],
                            "error": result["error_code"],
                        }
                    ),
                    flush=True,
                )
                if result["state"] != "succeeded":
                    raise RuntimeError(
                        "Read suite stopped at its first unsuccessful result"
                    )
                return result
            time.sleep(1)
        raise RuntimeError(
            "Read suite observation window ended; remaining test work is held"
        )

    try:
        for kind in args.kind or ("profile", "relationship", "following", "posts"):
            enqueue(kind, args.target)
        with Session(engine) as db, db.begin():
            row = db.scalar(
                select(Account).where(Account.id == account_id).with_for_update()
            )
            jobs = db.scalars(
                select(Job)
                .where(
                    Job.account_id == account_id, Job.state.in_(["queued", "running"])
                )
                .with_for_update()
            ).all()
            if (
                not row.paused
                or row.session_state != "connected"
                or row.restricted
                or row.live_acquisition_enabled
                or row.live_cleanup_enabled
                or db.scalar(
                    select(InsightWatch.id)
                    .where(InsightWatch.account_id == account_id, InsightWatch.enabled)
                    .limit(1)
                )
                or db.scalar(
                    select(Source.id)
                    .where(
                        Source.account_id == account_id,
                        Source.enabled,
                        Source.schedule_enabled,
                    )
                    .limit(1)
                )
                or db.scalar(
                    select(AccountConnection.id)
                    .where(
                        AccountConnection.account_id == account_id,
                        AccountConnection.state.in_(ACTIVE),
                    )
                    .limit(1)
                )
                or {job.id for job in jobs} != {check["job_id"] for check in checks}
            ):
                raise RuntimeError(
                    "Account or queue changed; read suite will not resume it"
                )
            row.paused = False
            db.add(
                AuditEvent(
                    account_id=account_id,
                    event="bounded_read_test_started",
                    detail={"target": args.target, "checks": len(checks)},
                )
            )
        unpaused = True
        for check in checks:
            wait(check)
        posts = next((result for result in results if result["kind"] == "posts"), None)
        post = (
            next((item for item in posts["items"] if item.get("url")), None)
            if posts
            else None
        )
        if post:
            wait(enqueue("likers", post["url"]))
        succeeded = True
    except (RuntimeError, OSError, ValueError, KeyError, SQLAlchemyError) as error:
        print(
            json.dumps({"suite_stopped": True, "reason_type": type(error).__name__}),
            flush=True,
        )
    finally:
        if unpaused:
            with Session(engine) as db, db.begin():
                db.scalar(
                    select(Account).where(Account.id == account_id).with_for_update()
                ).paused = True
                db.add(
                    AuditEvent(
                        account_id=account_id,
                        event="bounded_read_test_paused",
                        detail={"checks": len(checks)},
                    )
                )
        for check in checks:
            current = api(f"/insight-checks/{check['id']}")
            if current["state"] == "queued":
                api(f"/jobs/{check['job_id']}/cancel", {})
        after = api(f"/overview?account_id={account_id}")["requests_used"]
        final_account = next(
            value
            for value in api("/accounts?mode=live")["items"]
            if value["id"] == account_id
        )
        print(
            json.dumps(
                {
                    "account": account["username"],
                    "target": args.target,
                    "request_usage_before": before,
                    "request_usage_after": after,
                    "suite_actual_requests": after - before,
                    "pause_restored": final_account["paused"],
                    "checks": [
                        {
                            key: result[key]
                            for key in (
                                "id",
                                "job_id",
                                "kind",
                                "target",
                                "state",
                                "error_code",
                                "requests_used",
                                "observed_at",
                                "profile",
                                "relationship",
                                "complete",
                                "note",
                            )
                        }
                        | {
                            "items_count": len(result["items"]),
                            "post_urls": [
                                item["url"]
                                for item in result["items"]
                                if item["kind"] == "post" and item["url"]
                            ],
                        }
                        for result in results
                    ],
                },
                indent=2,
            ),
            flush=True,
        )
    return succeeded


if __name__ == "__main__":
    raise SystemExit(0 if main() else 1)
