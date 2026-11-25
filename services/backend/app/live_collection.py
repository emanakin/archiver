"""Persist bounded read observations without inferring absent relationships."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from .execution_errors import OperationBlocked, PolicySkip
from .models import (
    Account,
    AccountSession,
    Discovery,
    Job,
    Relationship,
    Source,
    Target,
    UsernameHistory,
)


def upsert_target(db, user, at):
    target = db.get(Target, user.id)
    if target is None:
        target = Target(id=user.id, username=user.username)
        db.add(target)
        db.flush()
        db.add(UsernameHistory(target_id=user.id, username=user.username, observed_at=at))
    elif target.username != user.username:
        target.username = user.username
        db.add(UsernameHistory(target_id=user.id, username=user.username, observed_at=at))
    return target


def collect(engine, lane, job_id, adapter, clock):
    with Session(engine) as db:
        job = db.get(Job, job_id)
        payload, account_id = dict(job.payload), job.account_id
        account = db.get(Account, account_id)
        if job.kind == "import_following":
            source_id, source_kind, source_value, resolved, order = (
                None,
                "following",
                None,
                account.instagram_id,
                "default",
            )
        else:
            source = db.get(Source, payload.get("source_id"))
            if source is None or source.account_id != account_id:
                raise OperationBlocked("Collection source is missing")
            if not source.enabled:
                raise PolicySkip("Source disabled before collection")
            source_id, source_kind, source_value = source.id, source.kind, source.value
            resolved, order = source.resolved_instagram_id, source.requested_order
        # Liker collection already gets a single response from Instagram.
        # Keep a larger sample from that response when the source requests it.
        page_limit = 100 if source_kind == "post_likers" else 10
        amount = min(page_limit, int(payload.get("remaining", 10)))
        cursor = payload.get("cursor") or None
    lane.assert_owned()
    if source_kind == "following":
        page = adapter.collect_following(resolved, cursor, amount)
    elif source_kind == "profile_followers":
        if not resolved:
            resolved = adapter.resolve_user(source_value).id
        page = adapter.collect_followers(resolved, cursor, amount, order)
    elif source_kind == "profile_mutuals":
        if not resolved:
            resolved = adapter.resolve_user(source_value).id
        page = adapter.collect_mutuals(resolved, cursor, amount)
    else:
        page = adapter.collect_likers(source_value, amount)
    lane.assert_owned()
    at = clock()
    with Session(engine) as db, db.begin():
        db.scalar(select(Account).where(Account.id == account_id).with_for_update())
        job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
        if job.claim_owner != lane.owner:
            raise OperationBlocked("Collection claim changed")
        for user in page.users:
            if source_kind == "profile_mutuals" and user.id == account.instagram_id:
                continue
            upsert_target(db, user, at)
            rel = db.scalar(
                select(Relationship).where(
                    Relationship.account_id == account_id, Relationship.target_id == user.id
                )
            )
            if rel is None:
                rel = Relationship(
                    account_id=account_id, target_id=user.id, observed_state="unknown"
                )
                db.add(rel)
                db.flush()
            if source_kind == "following":
                rel.observed_state, rel.ever_followed, rel.last_checked_at = "following", True, at
                rel.follow_confirmed_at = rel.follow_confirmed_at or at
                rel.version += 1
                # Never adopt an imported connection or unset existing management.
            else:
                link = db.scalar(
                    select(Discovery).where(
                        Discovery.source_id == source_id, Discovery.target_id == user.id
                    )
                )
                if link:
                    link.last_seen_at = at
                else:
                    db.add(
                        Discovery(
                            source_id=source_id,
                            target_id=user.id,
                            first_seen_at=at,
                            last_seen_at=at,
                        )
                    )
                if (
                    rel.observed_state == "unknown"
                    and not rel.protected
                    and not rel.excluded
                    and not rel.ever_followed
                ):
                    active = db.scalar(
                        select(Job.id)
                        .where(
                            Job.account_id == account_id,
                            Job.target_id == user.id,
                            Job.kind == "reconcile",
                            Job.state.in_(["queued", "running"]),
                        )
                        .limit(1)
                    )
                    if not active:
                        db.add(
                            Job(
                                account_id=account_id,
                                target_id=user.id,
                                kind="reconcile",
                                priority=-1,
                                idempotency_key=f"observe:{job.id}:{user.id}",
                            )
                        )
        remaining = max(0, int(job.payload.get("remaining", 10)) - len(page.users))
        continuing = bool(
            source_kind != "profile_mutuals" and remaining and page.next_cursor and page.users
        )
        if continuing:
            db.add(
                Job(
                    account_id=account_id,
                    kind=job.kind,
                    idempotency_key=f"page:{job.id}",
                    payload={**job.payload, "cursor": page.next_cursor, "remaining": remaining},
                )
            )
        metadata = db.get(AccountSession, account_id)
        if source_kind == "following":
            if metadata:
                metadata.import_cursor, metadata.import_complete = page.next_cursor, page.complete
        else:
            source = db.scalar(select(Source).where(Source.id == source_id).with_for_update())
            source.resolved_instagram_id = resolved
            source.collection_cursor, source.collection_complete = page.next_cursor, page.complete
            source.collection_note, source.last_collected_at = page.note, at
            source.collection_state = (
                "running" if continuing else "completed" if page.complete else "partial"
            )
            if metadata and page.users:
                metadata.capabilities = {
                    **metadata.capabilities,
                    "bounded_collection_verified": True,
                }
