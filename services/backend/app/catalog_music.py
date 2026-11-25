"""Read catalog-track metadata from a pinned media-info response."""

import re


def catalog_identifier(value):
    value = str(value)
    return value if re.fullmatch(r"[1-9][0-9]{0,31}", value) else None


def feed_track(asset, cluster, title, artist, start=0):
    asset, cluster = catalog_identifier(asset), catalog_identifier(cluster)
    if not asset or not cluster or not title or not artist:
        return None
    return {
        "audio_asset_id": asset,
        "audio_cluster_id": cluster,
        "title": str(title)[:250],
        "artist": str(artist)[:250],
        "start_ms": start if type(start) is int and 0 <= start <= 86_400_000 else 0,
    }


def observed_music_asset(client, media_id):
    response = getattr(client, "last_json", None)
    items = response.get("items") if isinstance(response, dict) else None
    if not isinstance(items, list) or not items or not isinstance(items[0], dict):
        return None
    item = items[0]
    pk = str(item.get("pk") or item.get("id") or "").split("_", 1)[0]
    if pk != str(media_id).split("_", 1)[0]:
        return None
    metadata = item.get("music_metadata")
    if not isinstance(metadata, dict):
        return None
    music = metadata.get("music_info") or metadata
    if not isinstance(music, dict):
        return None
    asset = music.get("music_asset_info")
    return music, asset if isinstance(asset, dict) else {}


def observed_track(client, media_id):
    music, asset = observed_music_asset(client, media_id) or ({}, {})
    track_id = str(asset.get("music_canonical_id") or music.get("music_canonical_id") or "")
    if not re.fullmatch(r"[0-9]{1,32}", track_id):
        return None
    return {
        "id": track_id,
        "title": str(asset.get("title") or music.get("title") or "")[:200],
        "artist": str(
            asset.get("display_artist")
            or asset.get("artist_name")
            or music.get("artist_name")
            or ""
        )[:200],
    }


def observed_feed_music(client, media_id):
    music, asset = observed_music_asset(client, media_id) or ({}, {})
    asset_id = asset.get("audio_asset_id") or music.get("audio_asset_id") or asset.get("id")
    cluster_id = asset.get("audio_cluster_id") or music.get("audio_cluster_id")
    if not re.fullmatch(r"[1-9][0-9]{0,31}", str(asset_id)):
        return None
    return {
        "audio_asset_id": str(asset_id),
        "audio_cluster_id": str(cluster_id)
        if re.fullmatch(r"[1-9][0-9]{0,31}", str(cluster_id))
        else None,
    }


def observed_source_music(client, media_id):
    music, asset = observed_music_asset(client, media_id) or ({}, {})
    canonical = observed_track(client, media_id)
    ids = observed_feed_music(client, media_id) or {}
    consumption = music.get("music_consumption_info") or {}
    start = (
        consumption.get("audio_asset_start_time_in_ms", 0) if isinstance(consumption, dict) else 0
    )
    title = asset.get("title") or music.get("title")
    artist = asset.get("display_artist") or asset.get("artist_name") or music.get("artist_name")
    native = feed_track(
        ids.get("audio_asset_id"),
        ids.get("audio_cluster_id"),
        title,
        artist,
        start,
    )
    if native:
        return {**native, "id": (canonical or {}).get("id")}
    if canonical:
        return canonical
    return (
        {"id": None, "title": str(title)[:250], "artist": str(artist)[:250]}
        if title and artist
        else None
    )


def source_feed_track(value):
    value = value or {}
    return feed_track(
        value.get("audio_asset_id"),
        value.get("audio_cluster_id"),
        value.get("title"),
        value.get("artist"),
        value.get("start_ms", 0),
    )
