"""Narrow, bounded pinned-client reads. Never return raw library responses."""

import re
from datetime import UTC, datetime

from .execution_errors import OperationBlocked, TransientRead
from .insight_schemas import InsightItem, InsightObservation, InsightProfile, InsightRelationship


def number(value):
    return value if type(value) is int and value >= 0 else None


def profile(adapter, value):
    return InsightProfile(
        id=adapter.identity(value.pk),
        username=value.username,
        full_name=str(getattr(value, "full_name", ""))[:150] or None,
        followers=number(getattr(value, "follower_count", None)),
        following=number(getattr(value, "following_count", None)),
        posts=number(getattr(value, "media_count", None)),
        is_private=getattr(value, "is_private", None),
    )


def media_item(adapter, value, kind, username):
    code = getattr(value, "code", None)
    url = (
        f"https://www.instagram.com/p/{code}/"
        if isinstance(code, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,100}", code)
        else None
    )
    identity = adapter.identity(value.pk)
    if kind == "story":
        url = f"https://www.instagram.com/stories/{username}/{identity}/"
    taken_at = getattr(value, "taken_at", None)
    if isinstance(taken_at, datetime) and taken_at.tzinfo is None:
        taken_at = taken_at.replace(tzinfo=UTC)
    return InsightItem(
        id=identity,
        kind=kind,
        username=username,
        url=url,
        caption=str(getattr(value, "caption_text", "") or "")[:2200] or None,
        media_type={1: "Photo", 2: "Video", 8: "Carousel"}.get(getattr(value, "media_type", None)),
        likes=number(getattr(value, "like_count", None)),
        comments=number(getattr(value, "comment_count", None)),
        posted_at=taken_at if isinstance(taken_at, datetime) else None,
    )


def read(adapter, kind, target, limit, cursor=None, known_user_id=None):
    if not 1 <= limit <= 20:
        raise OperationBlocked("Insight observation must be bounded")
    client = adapter.client
    if kind == "likers":
        page = adapter.collect_likers(target, limit)
        return InsightObservation(
            items=[
                InsightItem(
                    id=user.id,
                    kind="user",
                    username=user.username,
                    url=f"https://www.instagram.com/{user.username}/",
                )
                for user in page.users
            ],
            complete=False,
            note="One bounded liker response. Missing users do not prove an unlike; exact like times and completeness are unavailable.",
        )
    account_profile = None
    if kind == "profile":
        user = (
            client.user_info_v1(adapter.identity(known_user_id))
            if known_user_id
            else client.user_info_by_username_v1(target)
        )
        return InsightObservation(
            profile=profile(adapter, user),
            complete=True,
            note="Counts reported by Instagram at observation time. This is not a growth-history or professional analytics report.",
        )
    if known_user_id:
        user_id = adapter.identity(known_user_id)
    else:
        user = client.user_info_by_username_v1(target)
        user_id, account_profile = adapter.identity(user.pk), profile(adapter, user)
    if kind == "relationship":
        relationship = adapter.relationship(user_id)
        return InsightObservation(
            profile=account_profile,
            relationship=InsightRelationship(
                target_id=user_id, state=relationship.state, follows_back=relationship.follows_back
            ),
            complete=True,
            note="Fresh relationship observation for the connected account. No follow or unfollow was sent.",
        )
    if kind in {"followers", "following"}:
        page = (
            adapter.collect_followers(user_id, cursor, limit)
            if kind == "followers"
            else adapter.collect_following(user_id, cursor, limit)
        )
        return InsightObservation(
            profile=account_profile,
            items=[
                InsightItem(
                    id=user.id,
                    kind="user",
                    username=user.username,
                    url=f"https://www.instagram.com/{user.username}/",
                )
                for user in page.users
            ],
            next_cursor=page.next_cursor,
            complete=page.complete,
            note=page.note
            or "Bounded account-list observation. Ordering may change; missing from this page does not establish an absent relationship.",
        )
    if kind == "posts":
        values, next_cursor = client.user_medias_paginated_v1(
            user_id, amount=limit, end_cursor=cursor or ""
        )
        if not isinstance(values, list) or (
            next_cursor
            and (
                not isinstance(next_cursor, str) or len(next_cursor) > 200 or next_cursor == cursor
            )
        ):
            raise TransientRead("Post page did not return a usable result or advancing cursor")
        return InsightObservation(
            profile=account_profile,
            items=[media_item(adapter, value, "post", target) for value in values[:limit]],
            next_cursor=next_cursor or None,
            complete=False,
            note="A bounded recent-post snapshot. A missing post may be unavailable to this account; pagination does not guarantee a complete history.",
        )
    if kind == "stories":
        values = client.user_stories_v1(user_id, amount=limit)
        if not isinstance(values, list):
            raise TransientRead("Stories did not return a validated list")
        return InsightObservation(
            profile=account_profile,
            items=[media_item(adapter, value, "story", target) for value in values[:limit]],
            complete=False,
            note="A bounded snapshot of accessible active stories. No story was marked as seen or changed.",
        )
    raise OperationBlocked("Unsupported insight capability")
