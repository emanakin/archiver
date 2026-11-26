"""Durable live execution infrastructure. The simulation worker never selects this implicitly."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from . import budgets
from .account_lane import AccountLane
from .cleanup import cleanup_due
from .content_publication import CONTENT_KINDS, WRITE_KINDS
from .dashboard import account_settings, release_unsent_follow
from .dm_intake import DM_KINDS, DM_REQUEST_RESERVATION
from .dm_receipts import RECEIPT, authorize_receipt, queue_receipt, send_receipt
from .execution_errors import (
    BudgetDayChanged,
    BudgetExhausted,
    ExecutionError,
    ManualPause,
    MutationRejected,
    OperationBlocked,
    OutcomePending,
    OwnershipLost,
    PolicySkip,
    Restriction,
    SessionRequired,
    TransientRead,
    UncertainWrite,
    WindowClosed,
)
from .models import (
    Account,
    Action,
    ActionAttempt,
    AttemptEvent,
    BudgetReservation,
    InsightCheck,
    Job,
    Relationship,
    Source,
    now,
)
from .policy import eligibility
from .timing import local_day, next_window, within_window


def aware(value):
    return value.replace(tzinfo=UTC) if value and value.tzinfo is None else value


def account_check(account, job, at, approved_test=False):
    if account.mode != "live":
        raise OperationBlocked("Simulation accounts cannot enter the live executor")
    connection_intent = job.kind in {"connect", "verify"}
    recovering = job.kind == "connect" and job.payload.get("recovery_acknowledged") is True
    if account.paused and not connection_intent:
        raise ManualPause("Account manually paused")
    if account.restricted and not recovering:
        raise Restriction("Account requires restriction recovery")
    if account.cooldown_until and aware(account.cooldown_until) > at:
        raise OperationBlocked("Account cooldown is active")
    if account.session_state != "connected" and not connection_intent:
        raise SessionRequired("Account session requires verification")
    if account.automatic_pause_reason and not connection_intent:
        raise OperationBlocked("Account recovery is required")
    if job.kind == "follow" and not account.live_acquisition_enabled and not approved_test:
        raise OperationBlocked("Live acquisition is disabled")
    if job.kind == "unfollow" and not account.live_cleanup_enabled and not approved_test:
        raise OperationBlocked("Live cleanup is disabled")
    if job.kind in {"follow", "unfollow"} | WRITE_KINDS and not account.read_only_verified_at:
        raise OperationBlocked("Read-only account validation is required")


class RequestGuard:
    """The adapter calls before_request immediately before EVERY outgoing HTTP request."""

    def __init__(self, engine, lane, job_id, attempt_id, clock=now):
        self.engine, self.lane, self.job_id, self.attempt_id, self.clock = (
            engine,
            lane,
            job_id,
            attempt_id,
            clock,
        )
        self.fatal_error = None

    def reserve_carousel_downloads(self, additional):
        try:
            self.reserve_dm_requests(additional)
        except BudgetExhausted:
            raise OperationBlocked("The carousel needs more remaining requests") from None

    def reserve_dm_requests(self, additional):
        self.lane.assert_owned()
        from .dm_intake import enabled_inbox

        at = self.clock()
        with Session(self.engine) as db, db.begin():
            account = db.scalar(
                select(Account).where(Account.id == self.lane.account_id).with_for_update()
            )
            job = db.scalar(select(Job).where(Job.id == self.job_id).with_for_update())
            if job.claim_owner != self.lane.owner or job.state != "running":
                raise OwnershipLost("The photo import no longer belongs to this executor")
            if job.kind not in {"dm_import", "dm_replacement_music"}:
                raise OperationBlocked(
                    "Only an approved DM media job can extend its request budget"
                )
            account_check(account, job, at)
            enabled_inbox(db, job)
            budgets.reserve(
                db, account.id, self.attempt_id, "requests", additional, at, extend=True
            )

    def owner_code_checkpoint(self, received=False, heartbeat_only=False):
        if getattr(self, "connection_check", None):
            self.connection_check()
        if self.fatal_error:
            raise self.fatal_error
        self.lane.assert_owned()
        at = self.clock()
        with Session(self.engine) as db, db.begin():
            account = db.scalar(
                select(Account).where(Account.id == self.lane.account_id).with_for_update()
            )
            job = db.scalar(select(Job).where(Job.id == self.job_id).with_for_update())
            if job.claim_owner != self.lane.owner or job.state != "running":
                raise OwnershipLost("Owner recovery claim no longer belongs to this executor")
            if not (
                job.kind == "connect"
                and (
                    job.payload.get("interactive_mfa")
                    or (
                        job.payload.get("recovery_acknowledged")
                        and job.payload.get("manual_profile_verification")
                    )
                )
            ):
                raise OperationBlocked("Only an explicit owner connection can request a code")
            account_check(account, job, at)
            job.heartbeat_at, job.lease_expires_at = at, at + timedelta(seconds=90)
            for reservation in db.scalars(
                select(BudgetReservation)
                .where(
                    BudgetReservation.attempt_id == self.attempt_id,
                    BudgetReservation.released.is_(False),
                )
                .with_for_update()
            ):
                reservation.expires_at = at + timedelta(minutes=2)
            if not heartbeat_only:
                db.add(
                    AttemptEvent(
                        attempt_id=self.attempt_id,
                        event="owner_code_received" if received else "owner_code_requested",
                        detail={},
                    )
                )

    def record_event(self, event, detail):
        # Diagnostics only. Values are screen names, flags and owner choices;
        # unknown keys are dropped so library payloads can never be journaled.
        allowed = {"stage", "screens", "session", "choice"}
        safe = {key: value for key, value in detail.items() if key in allowed}
        with Session(self.engine) as db, db.begin():
            db.add(AttemptEvent(attempt_id=self.attempt_id, event=event, detail=safe))

    def before_request(
        self, mutation=False, operation=None, target_id=None, media=None, receipt=None
    ):
        if self.fatal_error:
            raise self.fatal_error
        try:
            if getattr(self, "connection_check", None):
                self.connection_check()
            self.lane.assert_owned()
            at = self.clock()
            with Session(self.engine) as db, db.begin():
                account = db.scalar(
                    select(Account).where(Account.id == self.lane.account_id).with_for_update()
                )
                job = db.scalar(select(Job).where(Job.id == self.job_id).with_for_update())
                attempt = db.get(ActionAttempt, self.attempt_id)
                if job.claim_owner != self.lane.owner or job.state != "running":
                    raise OwnershipLost("Job claim no longer belongs to this executor")
                from .relationship_tests import approved_case

                test_case = approved_case(db, job, at)
                account_check(account, job, at, approved_test=bool(test_case))
                config = account_settings(db, account.id)
                if (
                    attempt.dispatch_started_at is None
                    and job.payload.get("ranked_expires_at")
                    and (
                        config["follow_pacing"] != "hourly"
                        or at >= datetime.fromisoformat(job.payload["ranked_expires_at"])
                    )
                ):
                    raise PolicySkip("Hourly follow slot expired or pacing changed")
                if (
                    attempt.dispatch_started_at is None
                    and job.payload.get("ranked_day")
                    and (
                        job.payload["ranked_day"] != local_day(at, config)
                        or job.payload["ranked_mode"] != config["ranked_follow_mode"]
                    )
                ):
                    raise PolicySkip("Ranked follow plan expired or changed")
                if job.kind not in {"connect", "verify"} and not within_window(at, config):
                    raise WindowClosed("Operating window is closed")
                if job.kind == "discover":
                    source = db.get(Source, job.payload.get("source_id"))
                    if source is None or source.account_id != account.id or not source.enabled:
                        raise PolicySkip("Collection source is disabled or missing")
                    if job.payload.get("recurring") and not source.schedule_enabled:
                        raise PolicySkip("Recurring collection was disabled")
                    if job.payload.get("recurring") and job.payload.get(
                        "run_local_day"
                    ) != local_day(at, config):
                        raise PolicySkip("Scheduled collection belongs to an earlier local day")
                if job.kind == "insight":
                    from .insight_watches import check_enabled

                    check_enabled(db, job)
                if job.kind in DM_KINDS:
                    from .dm_intake import enabled_inbox

                    enabled_inbox(db, job)
                from .content_publication import CONTENT_KINDS, guard_media, validate_intent

                if job.kind in CONTENT_KINDS:
                    validate_intent(db, job)
                if media is not None:
                    if not getattr(self, "allow_media", False):
                        raise OperationBlocked("This worker cannot send media actions")
                    guard_media(db, job, attempt, media, self, at)
                if receipt is not None:
                    if not getattr(self, "allow_dm_receipt", False):
                        raise OperationBlocked("This worker cannot send DM receipts")
                    authorize_receipt(db, account, job, attempt, receipt, at)
                if mutation:
                    if job.kind not in {"follow", "unfollow"}:
                        raise OperationBlocked(
                            "Read and connection jobs cannot mutate relationships"
                        )
                    if operation is not None and (
                        operation != job.kind or target_id != job.target_id
                    ):
                        raise OperationBlocked("Mutation does not match the approved job")
                    if attempt.dispatch_started_at is not None:
                        raise UncertainWrite(
                            "A mutation has already been dispatched by this attempt"
                        )
                    if job.kind == "follow" and config["follow_pacing"] == "hourly":
                        from .ranked_follows import hourly_slots

                        slots = hourly_slots(account.id, at, config)
                        hour = at.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
                        sent = db.scalars(
                            select(Job.id).where(
                                Job.account_id == account.id,
                                Job.kind == "follow",
                                Job.dispatch_started_at >= hour,
                                Job.dispatch_started_at <= at,
                            )
                        ).all()
                        if len(sent) >= len(slots):
                            raise PolicySkip("Hourly follow allowance exhausted")
                    rel = db.scalar(
                        select(Relationship)
                        .where(
                            Relationship.account_id == account.id,
                            Relationship.target_id == job.target_id,
                        )
                        .with_for_update()
                    )
                    reason = eligibility(job.kind, rel)
                    if job.payload.get("ranked_day") and rel and rel.follows_back is not False:
                        raise PolicySkip(
                            "Candidate already follows back or the observation is unavailable"
                        )
                    if reason:
                        raise PolicySkip(reason)
                    uncertain = db.scalar(
                        select(Job.id)
                        .where(
                            Job.account_id == account.id,
                            Job.target_id == job.target_id,
                            Job.kind == job.kind,
                            Job.id != job.id,
                            Job.dispatch_started_at.is_not(None),
                            Job.state.in_(["unknown", "blocked"]),
                            Job.error == "OUTCOME_UNKNOWN",
                        )
                        .limit(1)
                    )
                    if uncertain:
                        raise OutcomePending("A previous mutation still requires reconciliation")
                    if job.kind == "unfollow" and not test_case:
                        due = cleanup_due(rel, config)
                        if due is None or due > at:
                            raise PolicySkip("Retention is not due or its start time is unknown")
                    if job.kind == "unfollow" and (
                        rel.follows_back is not False
                        or rel.follows_back_observed_at is None
                        or aware(rel.follows_back_observed_at) < aware(attempt.started_at)
                    ):
                        raise PolicySkip(
                            "Follow-back status must be freshly confirmed as nonreciprocal"
                        )
                    budgets.consume(
                        db,
                        account.id,
                        attempt.id,
                        "follows" if job.kind == "follow" else "unfollows",
                        at,
                    )
                    attempt.dispatch_started_at = job.dispatch_started_at = at
                    db.add(
                        AttemptEvent(
                            attempt_id=attempt.id,
                            event="dispatch_started",
                            detail={"operation": job.kind},
                        )
                    )
                budgets.consume(db, account.id, attempt.id, "requests", at)
                attempt.request_count += 1
                job.heartbeat_at = at
                job.lease_expires_at = at + timedelta(seconds=90)
                db.add(
                    AttemptEvent(
                        attempt_id=attempt.id,
                        event="request_started",
                        created_at=at,
                        detail={
                            "mutation": mutation or receipt is not None,
                            "ordinal": attempt.request_count,
                        },
                    )
                )
        except Exception as exc:
            # Library prechecks can swallow exceptions. The next attempted call
            # still fails before transport, even if the helper caught this one.
            self.fatal_error = exc
            raise


class Executor:
    def __init__(self, engine, adapter_factory, clock=now, allow_mutations=True):
        self.engine, self.adapter_factory, self.clock = engine, adapter_factory, clock
        self.allow_mutations = allow_mutations

    def run_once(self):
        at = self.clock()
        with Session(self.engine) as db:
            accounts = db.scalars(
                select(Job.account_id)
                .join(Account, Account.id == Job.account_id)
                .where(
                    Account.mode == "live",
                    Job.kind != "compose_caption",
                    or_(
                        (Job.state == "queued")
                        & (Job.scheduled_at <= at)
                        & Account.paused.is_(False),
                        (Job.state == "running") & (Job.lease_expires_at <= at),
                    ),
                )
                .distinct()
                .limit(20)
            ).all()
        return any(self.run_account(account_id) for account_id in accounts)

    def recover(self, db, account_id):
        at = self.clock()
        account = db.scalar(select(Account).where(Account.id == account_id).with_for_update())
        for job in db.scalars(
            select(Job)
            .where(
                Job.account_id == account_id,
                Job.state == "running",
                Job.lease_expires_at <= at,
                Job.kind != "compose_caption",
            )
            .with_for_update()
        ):
            dispatched = job.dispatch_started_at is not None
            for attempt in db.scalars(
                select(ActionAttempt).where(
                    ActionAttempt.job_id == job.id, ActionAttempt.completed_at.is_(None)
                )
            ):
                attempt.completed_at = at
                attempt.outcome = "unknown" if dispatched else "interrupted"
                attempt.error_code = "OUTCOME_UNKNOWN" if dispatched else "CLAIM_EXPIRED"
                budgets.release(db, account.id, attempt.id)
                db.add(
                    AttemptEvent(
                        attempt_id=attempt.id,
                        event="claim_recovered",
                        detail={"dispatched": dispatched},
                    )
                )
            job.state = "unknown" if dispatched else "queued"
            job.error = "OUTCOME_UNKNOWN" if dispatched else None
            from .content_publication import CONTENT_KINDS, finish_draft

            if job.kind in CONTENT_KINDS:
                job.state, job.error = (
                    ("unknown", "OUTCOME_UNKNOWN") if dispatched else ("blocked", "CLAIM_EXPIRED")
                )
                finish_draft(db, job, job.state)
            if job.kind in {"connect", "verify"}:
                job.state, job.error, job.completed_at = "blocked", "SESSION_REQUIRED", at
            job.claim_owner = None
            job.lease_expires_at = None

    def claim(self, db, account_id, owner):
        at = self.clock()
        account = db.scalar(select(Account).where(Account.id == account_id).with_for_update())
        if account.mode != "live" or account.paused:
            return None
        # A lost advisory connection does not prove an old HTTP call has ended.
        # recover() handles expired leases first; honor every surviving claim
        # before allowing this new owner to send work for the same account.
        if db.scalar(
            select(Job.id)
            .where(
                Job.account_id == account_id, Job.state == "running", Job.kind != "compose_caption"
            )
            .limit(1)
        ):
            return None
        config = account_settings(db, account_id)
        if not within_window(at, config):
            return None
        job = db.scalar(
            select(Job)
            .where(
                Job.account_id == account_id,
                Job.state == "queued",
                Job.scheduled_at <= at,
                Job.kind != "compose_caption",
            )
            .order_by(Job.priority.desc(), Job.scheduled_at, Job.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if job is None:
            return None
        job.state, job.claim_owner = "running", owner
        job.started_at = job.started_at or at
        job.heartbeat_at, job.lease_expires_at = at, at + timedelta(seconds=90)
        job.attempt_count += 1
        attempt = ActionAttempt(job_id=job.id, account_id=account_id, owner=owner, started_at=at)
        db.add(attempt)
        db.flush()
        db.add(AttemptEvent(attempt_id=attempt.id, event="claimed", detail={}))
        return job.id, attempt.id

    def run_account(self, account_id):
        with AccountLane(self.engine, account_id) as lane:
            if not lane.acquired:
                return False
            with Session(self.engine) as db, db.begin():
                self.recover(db, account_id)
                claimed = self.claim(db, account_id, lane.owner)
            if not claimed:
                return False
            job_id, attempt_id = claimed
            guard = RequestGuard(self.engine, lane, job_id, attempt_id, self.clock)
            try:
                with Session(self.engine) as db, db.begin():
                    account = db.scalar(
                        select(Account).where(Account.id == account_id).with_for_update()
                    )
                    job = db.get(Job, job_id)
                    from .relationship_tests import approved_case

                    account_check(
                        account,
                        job,
                        self.clock(),
                        approved_test=bool(approved_case(db, job, self.clock())),
                    )
                    if (
                        job.kind
                        not in {
                            "follow",
                            "unfollow",
                            "reconcile",
                            "discover",
                            "import_following",
                            "insight",
                            "import_source_photo",
                            "publish_photo",
                            "publish_story",
                            "publish_carousel",
                            "publish_reel",
                            "edit_caption",
                            "delete_story",
                            "delete_post",
                            "archive_post",
                            "reconcile_content",
                            RECEIPT,
                        }
                        | DM_KINDS
                    ):
                        raise OperationBlocked("Capability is not implemented by this executor")
                    if (
                        job.kind in {"follow", "unfollow", RECEIPT} | WRITE_KINDS
                        and not self.allow_mutations
                    ):
                        raise OperationBlocked(
                            "Worker is configured for read-only Instagram access"
                        )
                    units = 4 if job.kind in {"follow", "unfollow"} else 1
                    if job.kind == "discover":
                        source = db.get(Source, job.payload.get("source_id"))
                        units = (
                            3
                            if source and source.kind == "post_likers"
                            else 2
                            if source and not source.resolved_instagram_id
                            else 1
                        )
                        if source and source.kind == "profile_mutuals":
                            from .schemas import MUTUAL_BATCH_LIMIT

                            units += min(MUTUAL_BATCH_LIMIT, int(job.payload["remaining"]))
                    if job.kind == "insight":
                        check = db.get(InsightCheck, job.payload.get("check_id"))
                        units = 1 if check and check.kind == "profile" else 3
                    if job.kind in DM_KINDS:
                        units = DM_REQUEST_RESERVATION
                    if job.kind == "import_source_photo":
                        units = 2
                    if job.kind in CONTENT_KINDS:
                        units = 4 if job.kind in WRITE_KINDS else 1
                        if (
                            job.kind == "reconcile_content"
                            and job.payload.get("parent_kind") == "archive_post"
                        ):
                            units = 2
                        if job.kind == "publish_carousel":
                            units = len(job.payload["child_upload_ids"]) + 2
                        elif job.kind == "publish_reel":
                            units = 6
                        if job.payload.get("snapshot", {}).get("music_canonical_id"):
                            units += 2
                        elif job.payload.get("snapshot", {}).get("playlist_music"):
                            units += 1
                    budgets.reserve(db, account_id, attempt_id, "requests", units, self.clock())
                    if job.kind in {"follow", "unfollow"}:
                        budgets.reserve(
                            db,
                            account_id,
                            attempt_id,
                            "follows" if job.kind == "follow" else "unfollows",
                            1,
                            self.clock(),
                        )
                    target_id, kind, payload = job.target_id, job.kind, dict(job.payload)
                adapter = self.adapter_factory(guard)
                guard.allow_media = self.allow_mutations and kind in WRITE_KINDS
                guard.allow_dm_receipt = self.allow_mutations and kind == RECEIPT
                if kind in CONTENT_KINDS:
                    from .content_publication import perform

                    perform(self.engine, lane, job_id, adapter, guard, self.clock)
                elif kind in DM_KINDS:
                    from .dm_intake import perform

                    perform(self.engine, lane, job_id, adapter, guard, self.clock)
                elif kind == RECEIPT:
                    send_receipt(self.engine, lane, job_id, adapter, payload)
                elif kind == "import_source_photo":
                    from .source_photo_import import perform

                    perform(self.engine, lane, job_id, adapter, guard)
                elif kind == "insight":
                    from .insights import perform

                    perform(self.engine, lane, job_id, adapter, self.clock)
                elif kind in {"discover", "import_following"}:
                    from .live_collection import collect

                    collect(self.engine, lane, job_id, adapter, self.clock)
                else:
                    observation = adapter.relationship(target_id)
                    self.observe(lane, job_id, observation)
                if kind in {"follow", "unfollow"}:
                    with Session(self.engine) as db:
                        rel = db.scalar(
                            select(Relationship).where(
                                Relationship.account_id == account_id,
                                Relationship.target_id == target_id,
                            )
                        )
                        reason = eligibility(kind, rel)
                        if reason:
                            raise PolicySkip(reason)
                        if kind == "unfollow" and observation.follows_back is not False:
                            raise PolicySkip("Follow-back is present or unknown")
                    result = (
                        adapter.follow(target_id)
                        if kind == "follow"
                        else adapter.unfollow(target_id)
                    )
                    if result.state not in (
                        {"following", "requested"} if kind == "follow" else {"none"}
                    ):
                        raise UncertainWrite("Mutation outcome needs reconciliation")
                    self.observe(lane, job_id, result, managed=True)
                elif kind == "reconcile" and payload.get("reconcile_job_id"):
                    self.reconcile(lane, payload["reconcile_job_id"], observation)
                if getattr(adapter, "persist", None):
                    adapter.persist()
                self.finish(lane, job_id, attempt_id, "succeeded")
            except OwnershipLost:
                # Leave committed dispatch evidence for the next owner; never
                # finalize another owner's job after losing the account lane.
                raise
            except Exception as exc:
                lane.assert_owned()
                with Session(self.engine) as db:
                    current = db.get(Job, job_id)
                    dispatched = current.dispatch_started_at is not None
                    scoped_test = (
                        bool(current.payload.get("relationship_test_id"))
                        or current.kind in CONTENT_KINDS
                    )
                code = (
                    exc.code
                    if isinstance(exc, ExecutionError)
                    else "OUTCOME_UNKNOWN"
                    if dispatched
                    else "READ_FAILED"
                )
                if dispatched and not isinstance(exc, MutationRejected):
                    state = "unknown"
                elif isinstance(exc, (BudgetExhausted, WindowClosed, ManualPause)):
                    state = "queued"
                elif isinstance(exc, PolicySkip):
                    state = "skipped"
                elif isinstance(exc, (Restriction, SessionRequired, OperationBlocked)):
                    state = "blocked"
                else:
                    state = "failed"
                if state == "queued" and scoped_test:
                    state = "blocked"
                self.finish(lane, job_id, attempt_id, state, code, exc)
            return True

    def observe(self, lane, job_id, snapshot, managed=False):
        lane.assert_owned()
        with Session(self.engine) as db, db.begin():
            db.scalar(select(Account).where(Account.id == lane.account_id).with_for_update())
            job = db.get(Job, job_id)
            if job.claim_owner != lane.owner:
                raise OwnershipLost("Claim changed during observation")
            rel = db.scalar(
                select(Relationship)
                .where(
                    Relationship.account_id == lane.account_id,
                    Relationship.target_id == job.target_id,
                )
                .with_for_update()
            )
            if rel is None:
                raise OperationBlocked("Relationship record is missing")
            self.apply_observation(rel, snapshot, self.clock(), managed)
            if rel.managed and snapshot.state == "following":
                rel.unfollow_due_at = cleanup_due(rel, account_settings(db, lane.account_id))

    @staticmethod
    def apply_observation(rel, snapshot, at, managed=False):
        if snapshot.state not in {"unknown", "none", "following", "requested"}:
            raise OperationBlocked("Unsupported relationship response")
        previous_state = rel.observed_state
        rel.observed_state, rel.last_checked_at = snapshot.state, at
        rel.follows_back, rel.follows_back_observed_at = snapshot.follows_back, at
        if snapshot.state in {"following", "requested"}:
            rel.ever_followed = True
            if snapshot.state == "following" and rel.follow_confirmed_at is None:
                rel.follow_confirmed_at = at
            if snapshot.state == "requested" and rel.follow_requested_at is None:
                rel.follow_requested_at = at
            if managed:
                rel.managed = True
        elif snapshot.state == "none" and (
            managed or (rel.managed and previous_state in {"following", "requested"})
        ):
            rel.unfollow_confirmed_at = at
            rel.managed = False
        rel.version += 1

    def reconcile(self, lane, parent_id, snapshot):
        lane.assert_owned()
        with Session(self.engine) as db, db.begin():
            db.scalar(select(Account).where(Account.id == lane.account_id).with_for_update())
            parent = db.scalar(
                select(Job)
                .where(Job.id == parent_id, Job.account_id == lane.account_id)
                .with_for_update()
            )
            if parent is None or parent.state not in {"unknown", "blocked"}:
                raise OperationBlocked("Original write is not awaiting reconciliation")
            confirmed = (
                parent.kind == "follow" and snapshot.state in {"following", "requested"}
            ) or (parent.kind == "unfollow" and snapshot.state == "none")
            if confirmed:
                rel = db.scalar(
                    select(Relationship)
                    .where(
                        Relationship.account_id == lane.account_id,
                        Relationship.target_id == parent.target_id,
                    )
                    .with_for_update()
                )
                self.apply_observation(
                    rel, snapshot, self.clock(), managed=parent.dispatch_started_at is not None
                )
                parent.state, parent.error, parent.completed_at = "succeeded", None, self.clock()
            else:
                # A negative read cannot prove a delayed mutation never happened.
                parent.state, parent.error = "blocked", "OUTCOME_UNKNOWN"
            summary = db.scalar(select(Action).where(Action.job_id == parent.id))
            if summary:
                summary.outcome, summary.detail = (
                    parent.state,
                    "Read confirmed the outcome without repeating a write"
                    if confirmed
                    else "OUTCOME_UNKNOWN",
                )
            original_attempt = db.scalar(
                select(ActionAttempt)
                .where(ActionAttempt.job_id == parent.id)
                .order_by(ActionAttempt.started_at.desc())
                .limit(1)
            )
            if original_attempt:
                db.add(
                    AttemptEvent(
                        attempt_id=original_attempt.id,
                        event="reconciled",
                        detail={"confirmed": confirmed, "state": snapshot.state},
                    )
                )

    def finish(self, lane, job_id, attempt_id, state, code=None, failure=None):
        lane.assert_owned()
        at = self.clock()
        with Session(self.engine) as db, db.begin():
            account = db.scalar(
                select(Account).where(Account.id == lane.account_id).with_for_update()
            )
            job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
            if job.claim_owner != lane.owner:
                raise OwnershipLost("Job owner changed before finalization")
            attempt = db.get(ActionAttempt, attempt_id)
            discovery_retry = None
            if state == "failed" and job.kind == "discover" and isinstance(failure, TransientRead):
                from .source_schedules import collection_retry_at

                discovery_retry = collection_retry_at(job, at, account_settings(db, account.id))
                if discovery_retry:
                    state = "queued"
            attempt.completed_at, attempt.outcome, attempt.error_code = at, state, code
            if job.kind == "follow" or failure is not None:
                from .follow_telemetry import record_follow_telemetry

                record_follow_telemetry(
                    db, account.id, attempt.id, at, code, getattr(failure, "failure_kind", None)
                )
            budgets.release(db, account.id, attempt.id)
            db.add(
                AttemptEvent(
                    attempt_id=attempt.id, event="completed", detail={"state": state, "code": code}
                )
            )
            job.state, job.error, job.claim_owner, job.lease_expires_at = state, code, None, None
            if isinstance(failure, PolicySkip):
                job.error = str(failure)
            job.completed_at = None if state == "queued" else at
            release_unsent_follow(db, job)
            if job.payload.get("relationship_test_id"):
                from .relationship_tests import advance

                advance(db, job, state, at)
            from .content_publication import PublicationCheckFailed, finish_draft

            if isinstance(failure, PublicationCheckFailed):
                job.payload = {**job.payload, "verification_detail": str(failure)}
                if job.kind == "reconcile_content":
                    parent = db.get(Job, job.payload.get("parent_id"))
                    if (
                        parent
                        and parent.account_id == account.id
                        and parent.state in {"unknown", "blocked"}
                    ):
                        parent.payload = {**parent.payload, "verification_detail": str(failure)}

            finish_draft(db, job, state)
            queue_receipt(db, account, job, state)
            if state == "queued":
                job.scheduled_at = discovery_retry or next_window(
                    at,
                    account_settings(db, account.id),
                    next_day=isinstance(failure, BudgetExhausted)
                    and not isinstance(failure, BudgetDayChanged),
                )
            if job.kind == "discover" and (
                discovery_retry or state in {"failed", "blocked", "skipped"}
            ):
                source = db.get(Source, job.payload.get("source_id"))
                if source and discovery_retry:
                    source.collection_state = "queued"
                    source.collection_note = "Transient read failure. A retry is scheduled."
                elif source:
                    source.collection_state = "idle" if state == "skipped" else "error"
                    source.collection_note = code
            if isinstance(failure, Restriction):
                account.restricted, account.automatic_pause_reason = True, "ACCOUNT_RESTRICTED"
            elif isinstance(failure, SessionRequired):
                account.session_state = "needs_verification"
                # A verification/client error must neither invent a restriction
                # nor erase a separate restriction already recorded earlier.
                if not account.restricted:
                    account.automatic_pause_reason = "SESSION_REQUIRED"
            action = db.scalar(select(Action).where(Action.job_id == job.id))
            if action is None:
                action = Action(
                    job_id=job.id,
                    kind=job.kind,
                    simulated=False,
                    outcome=state,
                    detail=job.error or "Verified operation completed",
                )
                db.add(action)
            else:
                action.outcome, action.detail = state, job.error or "Verified operation completed"
