"""Run one explicitly owner-approved pair, then restore pause and allowances.

Run inside the installed API container via stdin. This is an acceptance tool,
not a scheduler. It refuses existing work, history, enabled watches/workflows,
or a changed settings revision. Never repeats a dispatched action.
"""

import argparse
import json
import time
import uuid
from datetime import timedelta
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import engine
from app.models import Account, AccountSettings, AuditEvent, InsightCheck, InsightWatch, Job, now
from app.relationship_tests import aware


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--account", required=True)
    parser.add_argument("--approved-target", required=True)
    args = parser.parse_args()
    config = settings()
    if config.adapter_mode != "live":
        raise SystemExit("The reviewed live transport is required for the approved pair")

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
            raise RuntimeError(f"Local acceptance API stopped with HTTP{error.code}") from None

    account = next(
        (
            a
            for a in api("/accounts?mode=live")["items"]
            if a["username"].lower() == args.account.lstrip("@").lower()
        ),
        None,
    )
    if (
        not account
        or not account["paused"]
        or account["session_state"] != "connected"
        or account["restricted"]
    ):
        raise SystemExit("Use a connected, unrestricted, paused account")
    aid = account["id"]
    target = args.approved_target.lstrip("@").lower()
    owned_jobs, case = set(), None
    original, owned_version = None, None
    before = api(f"/overview?account_id={aid}")["requests_used"]

    def idle(db, allowed=()):
        row = db.scalar(select(Account).where(Account.id == aid).with_for_update())
        pending = set(
            db.scalars(
                select(Job.id).where(Job.account_id == aid, Job.state.in_(["queued", "running"]))
            )
        )
        if row.live_acquisition_enabled or row.live_cleanup_enabled or pending - set(allowed):
            raise RuntimeError("Existing workflows/work must be reviewed before this isolated test")
        if db.scalar(
            select(InsightWatch.id)
            .where(InsightWatch.account_id == aid, InsightWatch.enabled.is_(True))
            .limit(1)
        ):
            raise RuntimeError("Pause recurring watches before this isolated test")
        return row

    def wait_job(job_id):
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            job = api(f"/jobs/{job_id}")
            if job["state"] not in {"queued", "running"}:
                if job["state"] != "succeeded":
                    raise RuntimeError("Test stopped at its first unsuccessful job")
                return
            time.sleep(1)
        raise RuntimeError("Test observation window ended; review without replaying writes")

    try:
        with Session(engine) as db, db.begin():
            idle(db)
            setting = db.scalar(
                select(AccountSettings).where(AccountSettings.account_id == aid).with_for_update()
            )
            if (
                not setting
                or setting.values.get("follows_per_day", 0)
                or setting.values.get("unfollows_per_day", 0)
            ):
                raise RuntimeError(
                    "This harness requires original follow/unfollow allowances of zero"
                )
            original = dict(setting.values)
            setting.values = original | {"follows_per_day": 1, "unfollows_per_day": 1}
            setting.version += 1
            owned_version = setting.version
            db.add(
                AuditEvent(
                    account_id=aid,
                    event="paired_test_allowance",
                    detail={"target": target, "follows": 1, "unfollows": 1},
                )
            )
            check = db.scalar(
                select(InsightCheck)
                .join(Job, Job.id == InsightCheck.job_id)
                .where(
                    InsightCheck.account_id == aid,
                    InsightCheck.kind == "relationship",
                    InsightCheck.target == target,
                    Job.state == "succeeded",
                )
                .order_by(InsightCheck.created_at.desc())
                .limit(1)
            )
            check_id = (
                check.id
                if check
                and check.observed_at
                and aware(check.observed_at) > now() - timedelta(minutes=45)
                else None
            )
        if not check_id:
            check = api(
                "/insight-checks",
                {
                    "account_id": aid,
                    "kind": "relationship",
                    "target": target,
                    "requested_limit": 1,
                    "idempotency_key": "paired-baseline:" + str(uuid.uuid4()),
                },
            )
            owned_jobs.add(check["job_id"])
            with Session(engine) as db, db.begin():
                idle(db, owned_jobs).paused = False
            wait_job(check["job_id"])
            with Session(engine) as db, db.begin():
                idle(db).paused = True
            check_id = check["id"]
        case = api("/relationship-tests", {"account_id": aid, "check_id": check_id})
        if case["state"] != "prepared":
            raise RuntimeError("This target already has a test; inspect it rather than replaying")
        case = api(f"/relationship-tests/{case['id']}/approve", {})
        owned_jobs.add(case["follow_job_id"])
        with Session(engine) as db, db.begin():
            idle(db, owned_jobs).paused = False
        deadline = time.monotonic() + 150
        while time.monotonic() < deadline:
            case = next(
                c
                for c in api(f"/relationship-tests?account_id={aid}")["items"]
                if c["id"] == case["id"]
            )
            if case["unfollow_job_id"]:
                owned_jobs.add(case["unfollow_job_id"])
            if case["state"] not in {"follow_pending", "unfollow_pending"}:
                break
            time.sleep(1)
        print(json.dumps({"target": target, "test": case}, indent=2), flush=True)
        return case["state"] == "restored"
    except (RuntimeError, OSError, ValueError, KeyError) as error:
        print(json.dumps({"test_stopped": True, "reason": str(error)}), flush=True)
        return False
    finally:
        with Session(engine) as db, db.begin():
            row = db.scalar(select(Account).where(Account.id == aid).with_for_update())
            row.paused = True
            setting = db.scalar(
                select(AccountSettings).where(AccountSettings.account_id == aid).with_for_update()
            )
            restored = original is not None and setting.version == owned_version
            if restored:
                setting.values, setting.version = original, setting.version + 1
            for job in db.scalars(
                select(Job).where(Job.id.in_(owned_jobs), Job.state == "queued").with_for_update()
            ):
                job.state, job.error = "blocked", "ACCEPTANCE_WINDOW_ENDED"
            db.add(
                AuditEvent(
                    account_id=aid,
                    event="paired_test_held",
                    detail={"target": target, "allowances_restored": restored},
                )
            )
        after = api(f"/overview?account_id={aid}")["requests_used"]
        print(
            json.dumps(
                {
                    "pause_restored": True,
                    "allowances_restored": restored,
                    "requests_before": before,
                    "requests_after": after,
                    "test_requests": after - before,
                }
            ),
            flush=True,
        )


if __name__ == "__main__":
    raise SystemExit(0 if main() else 1)
