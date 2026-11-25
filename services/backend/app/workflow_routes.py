from datetime import UTC

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .cleanup import cleanup_due
from .config import settings
from .dashboard import account_settings, relationship_view, required, row
from .db import get_db
from .models import Account, AccountSession, AuditEvent, Job, Relationship, now
from .schemas import (
    AccountResponse,
    AdoptionInput,
    FollowTelemetry,
    RelationshipResponse,
    WorkflowPatch,
)

router = APIRouter(prefix="/api/v1")


@router.get("/accounts/{account_id}/follow-telemetry", response_model=FollowTelemetry)
def telemetry(account_id: str, db: Session = Depends(get_db)):
    from .follow_telemetry import follow_telemetry

    required(db, Account, account_id)
    return follow_telemetry(db, account_id, now())


@router.patch("/accounts/{account_id}/workflows", response_model=AccountResponse)
def workflow(account_id: str, body: WorkflowPatch, db: Session = Depends(get_db)):
    account = db.scalar(select(Account).where(Account.id == account_id).with_for_update())
    if account is None:
        raise HTTPException(404, "Not found")
    if account.workflow_version != body.expected_version:
        raise HTTPException(409, "Stale version; refresh workflow settings")
    if body.enabled:
        if not body.acknowledged:
            raise HTTPException(422, "Explicit workflow acknowledgement is required")
        metadata = db.get(AccountSession, account_id)
        cooldown = account.cooldown_until
        if cooldown and cooldown.tzinfo is None:
            cooldown = cooldown.replace(tzinfo=UTC)
        if (
            account.mode != "live"
            or settings().adapter_mode != "live"
            or account.session_state != "connected"
            or account.restricted
            or account.automatic_pause_reason
            or (cooldown and cooldown > now())
            or not account.read_only_verified_at
            or metadata is None
            or not metadata.capabilities.get("bounded_collection_verified")
        ):
            raise HTTPException(
                409,
                "A verified live account and successful bounded read-only collection are required",
            )
        config = account_settings(db, account_id)
        category = "follows_per_day" if body.workflow == "acquisition" else "unfollows_per_day"
        ceiling = config["request_ceiling"]
        if (config[category] is not None and config[category] < 1) or (
            ceiling is not None and ceiling < 4
        ):
            raise HTTPException(
                409,
                "Configure positive action and request allowances before enabling this workflow",
            )
    attribute = (
        "live_acquisition_enabled" if body.workflow == "acquisition" else "live_cleanup_enabled"
    )
    setattr(account, attribute, body.enabled)
    account.workflow_version += 1
    if body.enabled:
        kind = "follow" if body.workflow == "acquisition" else "unfollow"
        # Only undispatched configuration-blocked jobs are eligible to resume.
        # Unknown writes always retain their reconciliation gate.
        for job in db.scalars(
            select(Job)
            .where(
                Job.account_id == account_id,
                Job.kind == kind,
                Job.state == "blocked",
                Job.error == "OPERATION_BLOCKED",
                Job.dispatch_started_at.is_(None),
            )
            .with_for_update()
        ):
            job.state, job.error, job.completed_at = "queued", None, None
    db.add(
        AuditEvent(
            account_id=account_id,
            event="workflow_changed",
            detail={
                "workflow": body.workflow,
                "enabled": body.enabled,
                "version": account.workflow_version,
            },
        )
    )
    db.commit()
    return row(account)


@router.post("/relationships/{relationship_id}/adopt", response_model=RelationshipResponse)
def adopt(relationship_id: str, body: AdoptionInput, db: Session = Depends(get_db)):
    existing = required(db, Relationship, relationship_id)
    account = db.scalar(select(Account).where(Account.id == existing.account_id).with_for_update())
    if account.mode != "live":
        raise HTTPException(
            409, "Adoption is available only for a real account's observed relationships"
        )
    rel = db.scalar(
        select(Relationship)
        .where(Relationship.id == relationship_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if rel.version != body.expected_version:
        raise HTTPException(409, "Stale version; refresh this relationship")
    if not body.acknowledged:
        raise HTTPException(422, "Explicit adoption acknowledgement is required")
    if rel.observed_state != "following" or rel.protected or rel.excluded:
        raise HTTPException(409, "Only an unprotected, nonexcluded existing follow can be adopted")
    if not rel.managed:
        rel.managed, rel.ever_followed, rel.adopted_at = True, True, now()
        rel.version += 1
        rel.unfollow_due_at = cleanup_due(rel, account_settings(db, rel.account_id))
        db.add(
            AuditEvent(
                account_id=rel.account_id,
                event="relationship_adopted",
                detail={
                    "target_id": rel.target_id,
                    "retention_starts_at": rel.adopted_at.isoformat(),
                },
            )
        )
        db.commit()
    return relationship_view(db, rel)
