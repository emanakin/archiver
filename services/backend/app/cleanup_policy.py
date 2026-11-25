"""Read-only cleanup explanations; dispatch still requires a fresh guarded read."""

from datetime import UTC, timedelta


def cleanup_due(relationship, config):
    anchor = relationship.adopted_at or relationship.follow_confirmed_at
    if anchor is None:
        return None
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=UTC)
    return anchor + timedelta(days=config["retention_days"])


def cleanup_assessment(relationship, config, at, uncertain=False):
    due = (
        cleanup_due(relationship, config)
        if relationship.managed and relationship.observed_state == "following"
        else None
    )
    if relationship.protected:
        state, reason = "protected", "Protected account; automatic cleanup is skipped."
    elif relationship.excluded:
        state, reason = "excluded", "Excluded account; automatic cleanup is skipped."
    elif uncertain:
        state, reason = (
            "outcome_unknown",
            "An earlier write needs reconciliation before another write.",
        )
    elif relationship.observed_state == "requested":
        state, reason = (
            "manual_review",
            "Pending outgoing request; review manually. Automatic cleanup does not cancel it.",
        )
    elif relationship.observed_state == "none":
        state, reason = (
            "not_following",
            "Not currently following. Permanent acquisition history is retained.",
        )
    elif not relationship.managed:
        state, reason = "unmanaged", "Unmanaged relationship; automatic cleanup is skipped."
    elif relationship.observed_state != "following":
        state, reason = (
            "needs_observation",
            "The relationship is unknown; a fresh observation is required.",
        )
    elif relationship.follows_back is True:
        state, reason = "follows_back", "Last observed following back; preserve this relationship."
    elif due is None:
        state, reason = (
            "retention_unknown",
            "Retention start is unknown; automatic cleanup cannot proceed.",
        )
    elif due > at:
        state, reason = (
            "retention_pending",
            "Retention has not elapsed. A fresh relationship check is required before cleanup.",
        )
    elif relationship.follows_back is None or relationship.follows_back_observed_at is None:
        state, reason = (
            "needs_observation",
            "Retention has elapsed, but follow-back status needs a fresh check.",
        )
    else:
        state, reason = (
            "due_for_check",
            "Retention has elapsed. Cleanup must recheck follow-back status and protections before any unfollow.",
        )
    return {"state": state, "reason": reason, "retention_due_at": due}
