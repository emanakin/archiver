"""Stream safe attempt metadata for a bounded observation window; never message/session data."""

import argparse
import json
import time
from datetime import timedelta

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Account, ActionAttempt, Job, now


def watch(username, seconds, interval):
    started = now()
    deadline = time.monotonic() + seconds
    seen = set()
    while True:
        with SessionLocal() as db:
            account = db.scalar(select(Account).where(Account.username == username))
            if not account:
                raise ValueError("Account not found")
            attempts = db.execute(
                select(ActionAttempt, Job.kind)
                .join(Job, Job.id == ActionAttempt.job_id)
                .where(
                    ActionAttempt.account_id == account.id,
                    ActionAttempt.completed_at >= started - timedelta(seconds=1),
                )
                .order_by(ActionAttempt.completed_at, ActionAttempt.id)
            ).all()
            for attempt, kind in attempts:
                if attempt.id in seen:
                    continue
                seen.add(attempt.id)
                print(
                    json.dumps(
                        {
                            "at": attempt.completed_at.isoformat(),
                            "kind": kind,
                            "outcome": attempt.outcome,
                            "error": attempt.error_code,
                            "requests": attempt.request_count,
                            "duration_seconds": round(
                                (attempt.completed_at - attempt.started_at).total_seconds(), 3
                            ),
                        }
                    ),
                    flush=True,
                )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(interval, remaining))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account", required=True)
    parser.add_argument("--seconds", type=int, default=120)
    parser.add_argument("--interval", type=int, default=10)
    args = parser.parse_args()
    if not 1 <= args.seconds <= 3600 or not 1 <= args.interval <= 60:
        parser.error("Use 1–3600 seconds and a 1–60 second interval")
    watch(args.account, args.seconds, args.interval)
