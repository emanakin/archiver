"""Accept one bounded real liker-watch sample, then pause watch and account."""

import argparse
import json
import time
from urllib.request import Request, urlopen

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import engine
from app.models import Account, InsightWatch, Job


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--account", required=True)
    parser.add_argument("--post", required=True)
    args = parser.parse_args()
    config = settings()

    def api(path, body=None, method=None):
        request = Request(
            "http://127.0.0.1:8000/api/v1" + path,
            data=json.dumps(body).encode() if body is not None else None,
            method=method,
            headers={
                "X-Internal-Token": config.internal_api_token,
                "Origin": config.app_origin,
                "Content-Type": "application/json",
            },
        )
        with urlopen(request, timeout=15) as response:
            return json.load(response)

    account = next(
        a
        for a in api("/accounts?mode=live")["items"]
        if a["username"].lower() == args.account.lstrip("@").lower()
    )
    aid = account["id"]
    with Session(engine) as db, db.begin():
        row = db.scalar(select(Account).where(Account.id == aid).with_for_update())
        if (
            not row.paused
            or row.session_state != "connected"
            or row.restricted
            or row.live_acquisition_enabled
            or row.live_cleanup_enabled
        ):
            raise SystemExit("Requires a connected paused account with broad workflows off")
        if db.scalar(
            select(Job.id)
            .where(Job.account_id == aid, Job.state.in_(["queued", "running"]))
            .limit(1)
        ) or db.scalar(
            select(InsightWatch.id)
            .where(InsightWatch.account_id == aid, InsightWatch.enabled.is_(True))
            .limit(1)
        ):
            raise SystemExit("Existing queue/watch work must be reviewed first")
    before = api(f"/overview?account_id={aid}")["requests_used"]
    watch = api(
        "/insight-watches",
        {"account_id": aid, "target": args.post, "interval_minutes": 60, "sample_size": 3},
    )
    if watch["enabled"] or watch["first_success_at"]:
        raise SystemExit("This watch already has evidence; do not repeat acceptance")
    succeeded = False
    try:
        watch = api(
            f"/insight-watches/{watch['id']}",
            {
                "expected_version": watch["version"],
                "enabled": True,
                "interval_minutes": 60,
                "sample_size": 3,
            },
            "PATCH",
        )
        with Session(engine) as db, db.begin():
            row = db.scalar(select(Account).where(Account.id == aid).with_for_update())
            # Only this watch can materialize; recheck the entire queue before resume.
            if not row.paused or db.scalar(
                select(Job.id)
                .where(Job.account_id == aid, Job.state.in_(["queued", "running"]))
                .limit(1)
            ):
                raise RuntimeError("Account queue changed before resume")
            row.paused = False
        deadline = time.monotonic() + 100
        while time.monotonic() < deadline:
            watch = next(
                w
                for w in api(f"/insight-watches?account_id={aid}")["items"]
                if w["id"] == watch["id"]
            )
            if watch["first_success_at"]:
                succeeded = True
                break
            with Session(engine) as db:
                failed = db.scalar(
                    select(Job.id)
                    .where(
                        Job.account_id == aid,
                        Job.kind == "insight",
                        Job.payload["watch_id"].as_string() == watch["id"],
                        Job.state.in_(["failed", "blocked", "unknown"]),
                    )
                    .limit(1)
                )
                if failed:
                    break
            time.sleep(1)
    finally:
        # Take the same account-first locks as scheduler/worker before holding
        # future samples. The hourly interval cannot run a second sample here.
        with Session(engine) as db, db.begin():
            db.scalar(select(Account).where(Account.id == aid).with_for_update()).paused = True
            current = db.scalar(
                select(InsightWatch).where(InsightWatch.id == watch["id"]).with_for_update()
            )
            current.enabled, current.version = False, current.version + 1
            for job in db.scalars(
                select(Job)
                .where(
                    Job.account_id == aid,
                    Job.kind == "insight",
                    Job.payload["watch_id"].as_string() == watch["id"],
                    Job.state == "queued",
                )
                .with_for_update()
            ):
                job.state, job.error = "cancelled", "ACCEPTANCE_WINDOW_ENDED"
        observations = api(f"/insight-watches/{watch['id']}/observations")
        after = api(f"/overview?account_id={aid}")["requests_used"]
        print(
            json.dumps(
                {
                    "watch_id": watch["id"],
                    "baseline_succeeded": succeeded,
                    "observed_accounts": observations["total"],
                    "all_baseline": all(item["baseline"] for item in observations["items"]),
                    "requests_before": before,
                    "requests_after": after,
                    "actual_requests": after - before,
                    "watch_paused": True,
                    "account_paused": True,
                }
            ),
            flush=True,
        )
    return succeeded


if __name__ == "__main__":
    raise SystemExit(0 if main() else 1)
