"""Simulation worker; one transaction per job. See docs/06-WORKER.md for live design."""

import logging
import signal
import threading
import time
import uuid

from sqlalchemy import select

from .config import settings
from .dashboard import mock_collect, release_unsent_follow
from .db import SessionLocal, engine
from .models import Account, Action, Job, Relationship, WorkerHeartbeat, now
from .policy import eligibility

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
stop = threading.Event()


def tick(db):
    job = db.scalar(
        select(Job)
        .join(Account, Account.id == Job.account_id)
        .where(
            Job.state == "queued",
            Job.scheduled_at <= now(),
            Account.paused.is_(False),
            Account.mode == "mock",
        )
        .order_by(Job.scheduled_at, Job.id)
        .with_for_update(skip_locked=True, of=Job)
        .limit(1)
    )
    if job is None:
        return False
    job.started_at = now()
    if job.kind == "discover":
        state, detail = mock_collect(db, job)
        reason = detail if state != "simulated" else None
    else:
        rel = db.scalar(
            select(Relationship)
            .where(
                Relationship.account_id == job.account_id, Relationship.target_id == job.target_id
            )
            .with_for_update()
        )
        reason = eligibility(job.kind, rel)
        state = "skipped" if reason else "simulated"
        detail = reason or "Simulation completed. No Instagram request was sent."
    job.completed_at = now()
    job.state = state
    job.error = reason
    release_unsent_follow(db, job)
    db.add(Action(job_id=job.id, kind=job.kind, outcome=job.state, simulated=True, detail=detail))
    # No observation or ever_followed flag is changed by simulation.
    db.commit()
    logging.info("job=%s state=%s", job.id, job.state)
    return True


def main():
    live_executor = None
    onboarding = onboarding_thread = None
    caption_thread = None
    if settings().adapter_mode != "mock":
        from .adapters.live_factory import LiveAdapterFactory
        from .executor import Executor

        live_executor = Executor(
            engine, LiveAdapterFactory(engine), allow_mutations=settings().adapter_mode == "live"
        )
        from .onboarding_worker import AccountOnboardingWorker

        onboarding = AccountOnboardingWorker(engine, stop)
        onboarding.publish_key()
        onboarding_thread = threading.Thread(
            target=onboarding.run, daemon=True, name="browser-onboarding"
        )
        onboarding_thread.start()
        from .caption_pipeline import run_one as compose_caption

        def run_captions():
            while not stop.is_set():
                try:
                    if compose_caption(engine):
                        continue
                except Exception:
                    logging.error("Caption worker iteration failed")
                stop.wait(3)

        caption_thread = threading.Thread(target=run_captions, daemon=True, name="caption-analysis")
        caption_thread.start()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    worker_id = str(uuid.uuid4())
    last_heartbeat = 0.0
    last_cleanup = 0.0
    last_sources = 0.0
    while not stop.is_set():
        try:
            with SessionLocal() as db:
                if time.monotonic() - last_heartbeat >= 15:
                    db.merge(WorkerHeartbeat(id=worker_id, observed_at=now()))
                    db.commit()
                    last_heartbeat = time.monotonic()
                    if onboarding:
                        onboarding.publish_key()
                if settings().adapter_mode == "mock":
                    tick(db)
            if time.monotonic() - last_sources >= 30:
                from .source_schedules import materialize_collections

                materialize_collections(
                    engine,
                    allow_live=settings().adapter_mode != "mock",
                    allow_mock=settings().adapter_mode == "mock",
                )
                if settings().adapter_mode != "mock":
                    from .insight_watches import materialize
                    from .spotify_playlist import refresh_due

                    materialize(engine)
                    refresh_due(engine)
                last_sources = time.monotonic()
            if settings().adapter_mode == "live":
                from .ranked_follows import materialize as ranked_follows

                ranked_follows(engine)
            if live_executor:
                if settings().adapter_mode == "live":
                    from .dm_intake import materialize_direct_polls

                    materialize_direct_polls(engine)
                if settings().adapter_mode == "live" and time.monotonic() - last_cleanup >= 30:
                    from .cleanup import materialize_cleanup

                    materialize_cleanup(engine)
                    last_cleanup = time.monotonic()
                live_executor.run_once()
        except Exception:
            # Library exceptions can contain session material. Persisted attempts
            # carry safe error codes; never render external exception bodies.
            logging.error("Worker iteration failed; inspect worker health and the attempt journal")
        stop.wait(settings().worker_poll_seconds)
    if onboarding_thread:
        onboarding_thread.join(timeout=35)
    if caption_thread:
        caption_thread.join(timeout=2)


if __name__ == "__main__":
    main()
