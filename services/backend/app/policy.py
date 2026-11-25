def eligibility(kind, relationship):
    if relationship is None:
        return "relationship_missing"
    if kind == "reconcile":
        return None
    if relationship.excluded:
        return "excluded"
    if kind == "follow":
        if relationship.protected:
            return "protected"
        if relationship.ever_followed:
            return "previously_followed"
        if relationship.observed_state in {"following", "requested"}:
            return "already_connected"
        if relationship.observed_state == "unknown":
            return "relationship_unknown"
    if kind == "unfollow":
        if relationship.protected:
            return "protected"
        if not relationship.managed:
            return "unmanaged"
        if relationship.observed_state != "following":
            return "not_confirmed_following"
    return None
