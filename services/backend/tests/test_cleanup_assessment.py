"""Cleanup explanations are observations, never permission to send a write."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.cleanup_policy import cleanup_assessment
from app.models import AccountSettings, Job
from app.schemas import SettingsValues

AT = datetime(2026, 11, 17, 12, tzinfo=UTC)


@pytest.mark.parametrize(
    "changes,state",
    [
        ({"protected": True}, "protected"),
        ({"excluded": True}, "excluded"),
        ({"observed_state": "requested"}, "manual_review"),
        ({"observed_state": "none"}, "not_following"),
        ({"managed": False}, "unmanaged"),
        ({"observed_state": "unknown"}, "needs_observation"),
        ({"follows_back": True}, "follows_back"),
        ({"follow_confirmed_at": None}, "retention_unknown"),
        ({"follow_confirmed_at": AT - timedelta(days=14)}, "retention_pending"),
        ({"follows_back": None}, "needs_observation"),
        ({"follows_back_observed_at": None}, "needs_observation"),
        ({}, "due_for_check"),
    ],
)
def test_cleanup_reasons_preserve_policy_and_history(changes, state):
    values = dict(
        managed=True,
        protected=False,
        excluded=False,
        observed_state="following",
        ever_followed=True,
        adopted_at=None,
        follow_confirmed_at=AT - timedelta(days=16),
        follows_back=False,
        follows_back_observed_at=AT - timedelta(days=1),
    )
    values.update(changes)
    relationship = SimpleNamespace(**values)
    result = cleanup_assessment(relationship, {"retention_days": 15}, AT)
    assert result["state"] == state
    assert vars(relationship) == values
    if state == "due_for_check":
        assert "recheck" in result["reason"] and "before any unfollow" in result["reason"]


def test_adoption_and_current_retention_determine_due_date():
    relationship = SimpleNamespace(
        managed=True,
        observed_state="following",
        protected=False,
        excluded=False,
        follows_back=False,
        follows_back_observed_at=AT,
        adopted_at=AT - timedelta(days=2),
        follow_confirmed_at=AT - timedelta(days=100),
    )
    result = cleanup_assessment(relationship, {"retention_days": 20}, AT)
    assert result["retention_due_at"] == AT + timedelta(days=18)
    assert result["state"] == "retention_pending"


def test_api_explains_unknown_writes_and_keeps_permanent_history(system):
    client, db, account, rel = system
    rel.managed, rel.ever_followed, rel.observed_state = True, True, "following"
    rel.follow_confirmed_at = AT - timedelta(days=16)
    rel.unfollow_due_at = AT - timedelta(days=1)  # Stale stored schedule must not win.
    db.add(
        AccountSettings(
            account_id=account.id, values=SettingsValues(retention_days=20).model_dump()
        )
    )
    uncertain = Job(
        account_id=account.id,
        target_id=rel.target_id,
        kind="unfollow",
        state="unknown",
        error="OUTCOME_UNKNOWN",
        dispatch_started_at=AT,
        idempotency_key="synthetic-uncertain-cleanup",
    )
    db.add(uncertain)
    db.commit()
    path = f"/api/v1/relationships?account_id={account.id}"
    item = client.get(path).json()["items"][0]
    assert item["cleanup"]["state"] == "outcome_unknown"
    assert datetime.fromisoformat(
        item["cleanup"]["retention_due_at"].replace("Z", "+00:00")
    ) == AT + timedelta(days=4)
    uncertain.state, uncertain.error = "succeeded", None
    rel.observed_state, rel.unfollow_confirmed_at = "none", AT
    db.commit()
    item = client.get(path).json()["items"][0]
    assert item["cleanup"]["state"] == "not_following"
    assert item["ever_followed"] and item["unfollow_confirmed_at"]
    assert rel.ever_followed and rel.follow_confirmed_at is not None
    protected = client.patch(
        f"/api/v1/relationships/{rel.id}", json={"expected_version": rel.version, "protected": True}
    ).json()
    assert protected["cleanup"]["state"] == "protected"
