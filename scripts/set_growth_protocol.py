"""Apply the owner's hourly acquisition settings to one already-enabled account."""

import json
import sys

from sqlalchemy import select

from app.dashboard import account_settings
from app.db import SessionLocal
from app.models import Account, AccountSettings, AuditEvent, Job, Source, now
from app.schemas import SettingsValues
from app.timing import local_day


def apply(username, sample_size=1000):
    with SessionLocal() as db, db.begin():
        account = db.scalar(select(Account).where(Account.username == username).with_for_update())
        if not account or not account.live_acquisition_enabled or account.mode != "live":
            raise ValueError("An already-enabled live acquisition account is required")
        sources = db.scalars(
            select(Source)
            .where(
                Source.account_id == account.id,
                Source.enabled.is_(True),
                Source.schedule_enabled.is_(True),
            )
            .order_by(Source.id)
            .with_for_update()
        ).all()
        mutuals = [s for s in sources if s.kind == "profile_mutuals"]
        others = [s for s in sources if s.kind != "profile_mutuals"]
        remaining = sample_size - 3 * len(mutuals)
        if not others or not len(others) <= remaining <= 100 * len(others):
            raise ValueError("The enabled source pool cannot supply the requested daily sample")
        base, extra = divmod(remaining, len(others))
        quotas = {s.id: base + (index < extra) for index, s in enumerate(others)}
        quotas.update({s.id: 3 for s in mutuals})
        stored = db.get(AccountSettings, account.id)
        previous = account_settings(db, account.id)
        values = {k: v for k, v in previous.items() if k != "version"}
        values.update(
            follow_pacing="hourly",
            follows_per_hour_min=75,
            follows_per_hour_max=100,
            follows_per_day=None,
            request_ceiling=None,
            collection_batch_size=100,
            ranked_follow_mode="eligible",
            window_start="00:00",
            window_end="00:00",
        )
        values = SettingsValues(**values).model_dump()
        if stored is None:
            stored = AccountSettings(account_id=account.id, values=values)
            db.add(stored)
        else:
            stored.values = values
            stored.version += 1
        at = now()
        day = local_day(at, values)
        for source in sources:
            old_limit = min(source.requested_limit, previous["collection_batch_size"])
            source.requested_limit = quotas[source.id]
            source.schedule_version += 1
            # Supplement a current-day run already queued or completed at the old sample size.
            daily_key = f"daily-collection:{source.id}:{day}"
            already = db.scalar(
                select(Job.id).where(
                    Job.account_id == account.id,
                    Job.idempotency_key == daily_key,
                )
            )
            delta = source.requested_limit - old_limit
            key = f"growth-sample:{day}:{source.id}:{sample_size}"
            if (
                already
                and delta > 0
                and not db.scalar(
                    select(Job.id).where(
                        Job.account_id == account.id,
                        Job.idempotency_key == key,
                    )
                )
            ):
                db.add(
                    Job(
                        account_id=account.id,
                        kind="discover",
                        idempotency_key=key,
                        payload={
                            "source_id": source.id,
                            "cursor": source.collection_cursor,
                            "remaining": delta,
                            "recurring": True,
                            "run_local_day": day,
                        },
                    )
                )
        # Re-plan any unsent automatic daily jobs under the new hourly policy.
        for job in db.scalars(
            select(Job)
            .where(
                Job.account_id == account.id,
                Job.kind == "follow",
                Job.state == "queued",
                Job.dispatch_started_at.is_(None),
            )
            .with_for_update()
        ):
            if job.payload.get("ranked_day"):
                from app.dashboard import release_unsent_follow

                job.state, job.completed_at, job.error = "cancelled", at, "pacing_changed"
                release_unsent_follow(db, job)
        detail = {
            "previous_settings": previous,
            "settings": values,
            "daily_sample_size": sum(quotas.values()),
            "source_quotas": quotas,
        }
        db.add(AuditEvent(account_id=account.id, event="growth_protocol_changed", detail=detail))
        return {
            "account": account.username,
            "daily_sample_size": sum(quotas.values()),
            "scheduled_sources": len(sources),
            "settings": values,
        }


if __name__ == "__main__":
    print(json.dumps(apply(sys.argv[1]), default=str))
