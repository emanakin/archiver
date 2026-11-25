"""Use a post's music cues to choose confirmed Instagram audio."""

import re

from .execution_errors import Inaccessible, TransientRead
from .spotify_playlist import FEATURE_CREDIT, feed_candidates, native_track, normalized

MUSIC_CONTEXT = re.compile(r"\b(?:songs?|tracks?|albums?|lyrics?|outro|soundtrack|music)\b", re.I)
QUOTED = re.compile(r'["“]([^"”\r\n]{2,100})["”]')
TITLE_LEAD = re.compile(
    r"(?:^|[.!?]\s+|\n)([A-Z0-9][\w’'-]+(?:[ \t]+(?:[A-Z0-9][\w’'-]+|of|the|and|in)){1,6})"
    r"\s+(?=talks?\b|closes?\b|opens?\b|is\b|was\b|appears?\b|by\b|from\b)"
)
ARTIST_HINT = re.compile(r"\bby\s+([A-Z][\w-]+(?:\s+[A-Z][\w-]+){0,2})\b")
POSSESSIVE = re.compile(r"\b([A-Z][\w-]{3,})[’']s?(?=\s)")


def named_song(caption):
    caption = caption[:2200]
    if not MUSIC_CONTEXT.search(caption):
        return None
    for pattern in (QUOTED, TITLE_LEAD):
        for match in pattern.finditer(caption):
            title = match[1].strip()
            nearby = caption[max(0, match.start() - 45) : match.end() + 160]
            hint = ARTIST_HINT.search(nearby) or POSSESSIVE.search(
                caption[match.end() : match.end() + 90]
            )
            if hint:
                return title, f"{title} {hint[1]}"[:260]
    if match := QUOTED.search(caption):
        return match[1].strip(), match[1].strip()
    return None


def named_track(adapter, title, query, caption, *, allow_partial=True):
    try:
        results = list(feed_candidates(adapter.client, query))
    except (Inaccessible, TransientRead, KeyError, ValueError):
        return None, "search_failed"
    expected = normalized(FEATURE_CREDIT.sub("", title))
    text = f" {normalized(caption)} ".replace(f" {expected} ", " ")
    exact = {}
    for row in results:
        if row.get("dark_message") or not (native := native_track(row)):
            continue
        if normalized(FEATURE_CREDIT.sub("", native["title"])) != expected:
            continue
        artist = normalized(native["artist"])
        first = artist.split()[0] if artist else ""
        strength = (
            2
            if f" {artist} " in text
            else 1
            if allow_partial and len(first) >= 4 and f" {first} " in text
            else 0
        )
        if strength:
            exact[native["audio_asset_id"], native["audio_cluster_id"]] = (strength, row, native)
    if not exact:
        return None, "no_match"
    best = max(strength for strength, _, _ in exact.values())
    choices = [(row, native) for strength, row, native in exact.values() if strength == best]
    if len(choices) == 1:
        return choices[0][1], "named_matched"
    if len(choices) == 2:
        (left, a), (right, b) = choices
        lengths = left.get("duration_in_ms"), right.get("duration_in_ms")
        flags = left.get("is_explicit"), right.get("is_explicit")
        if (
            normalized(a["artist"]) == normalized(b["artist"])
            and normalized(a["title"]) == normalized(b["title"])
            and all(type(flag) is bool for flag in flags)
            and flags[0] != flags[1]
            and all(type(length) is int and length > 0 for length in lengths)
            and abs(lengths[0] - lengths[1]) <= 1000
        ):
            explicit = not re.search(r"\b(?:clean|censored) (?:version|edit)\b", caption, re.I)
            return a if flags[0] == explicit else b, "named_matched"
    return None, "ambiguous"
