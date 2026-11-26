"""Read public playlist metadata and match unused songs in Instagram's catalog."""

import hashlib
import json
import random
import re
import unicodedata
from datetime import UTC, datetime, timedelta
from urllib.error import URLError
from urllib.request import ProxyHandler, Request, build_opener

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field, field_validator
from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from .catalog_music import catalog_identifier, feed_track
from .config import settings
from .dashboard import account_settings, required
from .db import get_db
from .dm_reader import NoRedirect
from .execution_errors import Inaccessible, TransientRead
from .models import Account, AccountSettings, PlaylistSelection, PlaylistSync, PlaylistTrack, now
from .schemas import MusicPlaylist, SettingsPatch, SettingsValues, Strict
from .workspace_preferences import preferences

router = APIRouter(prefix="/api/v1")
TRACK_URI = re.compile(r"spotify:track:([A-Za-z0-9]{22})")
SEARCH_LIMIT = 3
FEATURE_CREDIT = re.compile(r"\s*[(\[]\s*(?:feat\.?|ft\.?)\s+[^)\]]+[)\]]\s*$", re.I)
MOOD_WORDS = tuple(
    frozenset(group.split())
    for group in (
        "ambient calm dreamy fog haze hazy late lonely night dusk quiet slow slowed still",
        "bold bright dance disco energy fast upbeat party rush summer sunny sunshine",
        "dark brooding cold shadow shadows noir tension storm night",
        "memory memories nostalgia nostalgic old past vintage retro yesterday childhood",
    )
)


def public_tracks(playlist_id):
    url = f"https://open.spotify.com/embed/playlist/{playlist_id}"
    request = Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "text/html"})
    try:
        with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=10) as reply:
            html = reply.read(2_000_000).decode()
        match = re.search(
            r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.S
        )
        data = json.loads(match[1])["props"]["pageProps"]["state"]["data"]["entity"]
        if data["id"] != playlist_id or data["type"] != "playlist":
            raise ValueError("Unexpected Spotify playlist")
        tracks = {}
        for item in data["trackList"][:100]:
            uri = TRACK_URI.fullmatch(item.get("uri", ""))
            title, artist = item.get("title", "").strip(), item.get("subtitle", "").strip()
            if uri and 1 <= len(title) <= 250 and 1 <= len(artist) <= 250:
                tracks[uri[1]] = {"spotify_id": uri[1], "title": title, "artist": artist}
    except (URLError, OSError, ValueError, TypeError, KeyError, IndexError, AttributeError):
        raise HTTPException(502, "Spotify did not return public playlist metadata") from None
    if not tracks:
        raise HTTPException(409, "Spotify did not expose any tracks for this playlist")
    return list(tracks.values())


def normalized(value):
    return " ".join(re.findall(r"[^\W_]+", unicodedata.normalize("NFKC", value).casefold()))


def song_key(title, artist):
    primary = re.split(r"[,;]", artist, maxsplit=1)[0]
    return hashlib.sha256(f"{normalized(title)}\0{normalized(primary)}".encode()).hexdigest()


def available_tracks(db, account_id, playlist_id):
    used = list(
        db.execute(
            select(PlaylistSelection.spotify_id, PlaylistSelection.song_key).where(
                PlaylistSelection.account_id == account_id
            )
        )
    )
    ids, keys = {item.spotify_id for item in used}, {item.song_key for item in used}
    pool = [playlist_id] if isinstance(playlist_id, str) else playlist_id
    rows = db.scalars(select(PlaylistTrack).where(PlaylistTrack.playlist_id.in_(pool)))
    available = []
    for row in rows:
        key = song_key(row.title, row.artist)
        if row.spotify_id not in ids and key not in keys:
            available.append(row)
            ids.add(row.spotify_id)
            keys.add(key)
    return available


def spotify_query(title, artist):
    primary = re.split(r"[,;]", artist, maxsplit=1)[0]
    return f"{FEATURE_CREDIT.sub('', title).strip()} {primary.strip()}"[:260]


def feed_candidates(client, query):
    response = client.music_search_v2(query, product="music_in_feed")
    if not isinstance(response, dict) or not isinstance(response.get("items"), list):
        raise TransientRead("Instagram did not return a music result list")
    for result in response["items"]:
        track = result.get("track") if isinstance(result, dict) else None
        if isinstance(track, dict):
            yield track


def native_track(track):
    highlights = track.get("highlight_start_times_in_ms") or []
    return feed_track(
        track.get("audio_asset_id"),
        track.get("audio_cluster_id"),
        track.get("title"),
        track.get("display_artist"),
        highlights[0] if isinstance(highlights, list) and highlights else 0,
    )


def match_track(
    adapter, tracks, used_catalog_ids, *, used_assets=(), used_clusters=(), checks=None
):
    for spotify_id, title, artist in tracks:
        expected_title = normalized(FEATURE_CREDIT.sub("", title))
        expected_artist = normalized(artist)
        primary = normalized(re.split(r"[,;]", artist, maxsplit=1)[0])
        try:
            results = list(feed_candidates(adapter.client, spotify_query(title, artist)))
        except (Inaccessible, TransientRead, KeyError, ValueError):
            if checks is not None:
                checks.append(
                    dict(spotify_id=spotify_id, title=title, artist=artist, state="search_failed")
                )
            continue
        exact = [
            track
            for track in results
            if normalized(FEATURE_CREDIT.sub("", str(track.get("title") or ""))) == expected_title
            and normalized(str(track.get("display_artist") or "")) in {expected_artist, primary}
        ]
        choices = {}
        for track in exact:
            if track.get("dark_message") or not (native := native_track(track)):
                continue
            choices[(native["audio_asset_id"], native["audio_cluster_id"])] = track, native
        state = "no_match"
        if len(choices) > 1:
            state = "ambiguous"
        elif exact and not choices:
            state = "missing_ids"
        elif choices:
            (asset, cluster), (track, native) = next(iter(choices.items()))
            canonical = catalog_identifier(track.get("music_canonical_id"))
            state = (
                "already_used"
                if canonical in used_catalog_ids or asset in used_assets or cluster in used_clusters
                else "matched"
            )
        if checks is not None:
            checks.append(dict(spotify_id=spotify_id, title=title, artist=artist, state=state))
        if state == "matched":
            return {
                "spotify_id": spotify_id,
                "song_key": song_key(title, artist),
                "catalog_id": canonical,
                "audio_asset_id": asset,
                "audio_cluster_id": cluster,
                "title": title,
                "artist": artist,
                "feed_music": native,
            }
    return None


def playlist_candidates(db, account_id, playlist_id, context=""):
    candidates = available_tracks(db, account_id, playlist_id)
    random.shuffle(candidates)
    tokens = set(normalized(context).split())
    context = f" {normalized(context)} "

    def score(row):
        title, artist = normalized(row.title), normalized(row.artist)
        letters = set(title.split())
        taste = sum(bool(tokens & mood) and bool(letters & mood) for mood in MOOD_WORDS)
        overlap = sum(len(word) >= 4 for word in letters & tokens)
        return 12 * (len(artist) >= 4 and f" {artist} " in context) + 4 * taste + overlap

    candidates.sort(key=score, reverse=True)
    return [(row.spotify_id, row.title, row.artist) for row in candidates[:SEARCH_LIMIT]]


class RecentSong(Strict):
    spotify_id: str
    title: str
    artist: str


class PlaylistResponse(Strict):
    playlist_id: str
    available: int
    selected: int
    recent: list[RecentSong]
    synced_at: datetime | None = None
    sync_state: str | None = None
    refresh_minutes: int


class LibraryEntry(MusicPlaylist):
    unused: int
    sync_state: str


class MusicLibrary(Strict):
    version: int
    playlists: list[LibraryEntry]


class LibraryUpdate(Strict):
    expected_version: int = Field(ge=1)
    playlists: list[MusicPlaylist] = Field(max_length=30)

    @field_validator("playlists")
    @classmethod
    def distinct(cls, value):
        return SettingsValues.distinct_playlists(value)


def configured_playlist(account_id, config=None):
    config = config or settings()
    if config.dm_default_account_id == account_id:
        return config.dm_spotify_playlist_id or None
    return None


def music_library(db, account_id, config=None):
    saved = db.get(AccountSettings, account_id)
    entries = (saved.values or {}).get("music_library") if saved else None
    if entries is not None:
        return [MusicPlaylist.model_validate(entry) for entry in entries]
    default = configured_playlist(account_id, preferences(db, account_id, config or settings()))
    return [MusicPlaylist(playlist_id=default, label="Original playlist")] if default else []


@router.get("/accounts/{account_id}/music-library", response_model=MusicLibrary)
def library_view(account_id: str, db: Session = Depends(get_db)):
    required(db, Account, account_id)
    items = []
    for entry in music_library(db, account_id):
        sync = db.get(PlaylistSync, entry.playlist_id)
        items.append(
            LibraryEntry(
                **entry.model_dump(),
                unused=len(available_tracks(db, account_id, entry.playlist_id)),
                sync_state=sync.state if sync else "pending",
            )
        )
    return MusicLibrary(version=account_settings(db, account_id)["version"], playlists=items)


@router.put("/accounts/{account_id}/music-library", response_model=MusicLibrary)
def update_library(account_id: str, body: LibraryUpdate, db: Session = Depends(get_db)):
    from .main import patch_settings

    values = account_settings(db, account_id)
    values.pop("version")
    values["music_library"] = body.playlists
    patch_settings(account_id, SettingsPatch(**values, expected_version=body.expected_version), db)
    return library_view(account_id, db)


@router.get("/accounts/{account_id}/spotify-playlist", response_model=PlaylistResponse | None)
def playlist_status(account_id: str, db: Session = Depends(get_db)):
    pool = [p.playlist_id for p in music_library(db, account_id) if p.enabled]
    if not pool:
        return None
    playlist_id = pool[0]
    history = list(
        db.scalars(
            select(PlaylistSelection)
            .where(PlaylistSelection.account_id == account_id)
            .order_by(PlaylistSelection.selected_at.desc())
        )
    )
    sync = db.get(PlaylistSync, playlist_id)
    return PlaylistResponse(
        playlist_id=playlist_id,
        available=len(available_tracks(db, account_id, pool)),
        selected=len(history),
        recent=[
            RecentSong(spotify_id=row.spotify_id, title=row.title, artist=row.artist)
            for row in history[:10]
        ],
        synced_at=sync.synced_at if sync else None,
        sync_state=sync.state if sync else None,
        refresh_minutes=settings().dm_spotify_refresh_minutes,
    )


def sync_playlist(
    engine, account_id, playlist_id, interval, *, manual=False, clock=now, workspace_id=None
):
    at = clock()
    with Session(engine, info={"workspace_id": workspace_id}) as db, db.begin():
        account = db.scalar(select(Account).where(Account.id == account_id).with_for_update())
        if not account:
            return False
        if engine.dialect.name == "postgresql":
            db.execute(
                text("select pg_advisory_xact_lock(hashtext(:key))"),
                {"key": f"spotify:{playlist_id}"},
            )
        sync = db.get(PlaylistSync, playlist_id)
        if sync:
            checked = sync.checked_at
            if checked.tzinfo is None:
                checked = checked.replace(tzinfo=UTC)
            if sync.state == "refreshing" and at - checked < timedelta(minutes=2):
                return False
            if not manual and sync.state != "refreshing" and at - checked < interval:
                return False
            sync.checked_at, sync.state = at, "refreshing"
        else:
            db.add(PlaylistSync(playlist_id=playlist_id, checked_at=at, state="refreshing"))
    try:
        tracks = public_tracks(playlist_id)
    except HTTPException as error:
        tracks, failure = None, error
    else:
        failure = None
    with Session(engine, info={"workspace_id": workspace_id}) as db, db.begin():
        db.scalar(select(Account).where(Account.id == account_id).with_for_update())
        sync = db.get(PlaylistSync, playlist_id)
        if not sync:
            return False
        checked = sync.checked_at if sync.checked_at.tzinfo else sync.checked_at.replace(tzinfo=UTC)
        if checked != at:
            return False
        if tracks:
            db.execute(delete(PlaylistTrack).where(PlaylistTrack.playlist_id == playlist_id))
            db.add_all(PlaylistTrack(playlist_id=playlist_id, **row) for row in tracks)
            sync.synced_at, sync.state = clock(), "ready"
        else:
            sync.state = "failed"
    if manual and failure:
        raise failure
    return True


def refresh_due(engine):
    config = settings()
    with Session(engine) as db:
        account_ids = db.scalars(select(Account.id).where(Account.mode == "live")).all()
        pairs = {
            p.playlist_id: aid
            for aid in account_ids
            for p in music_library(db, aid, config)
            if p.enabled
        }
    for playlist_id, account_id in pairs.items():
        if sync_playlist(
            engine, account_id, playlist_id, timedelta(minutes=config.dm_spotify_refresh_minutes)
        ):
            break


@router.post("/accounts/{account_id}/spotify-playlist/refresh", response_model=PlaylistResponse)
def refresh_playlist(
    account_id: str, db: Session = Depends(get_db), playlist_id: str | None = None
):
    entries = music_library(db, account_id)
    if playlist_id is None:
        playlist_id = next((p.playlist_id for p in entries if p.enabled), None)
    if not playlist_id or playlist_id not in {p.playlist_id for p in entries if p.enabled}:
        raise HTTPException(404, "No Spotify playlist is configured for this account")
    required(db, Account, account_id)
    db.rollback()
    if not sync_playlist(
        db.get_bind(),
        account_id,
        playlist_id,
        timedelta(),
        manual=True,
        workspace_id=db.info.get("workspace_id"),
    ):
        raise HTTPException(409, "The Spotify playlist is already refreshing")
    status = playlist_status(account_id, db)
    if status is None:
        raise HTTPException(409, "The playlist rotation changed during refresh")
    return status
