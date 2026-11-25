"""Read a bounded direct thread and fetch a photo resolved by Instagram."""

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.error import URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .catalog_music import observed_source_music, observed_track
from .content_drafts import MAX_CAROUSEL_PHOTOS
from .content_storage import MAX_BYTES
from .dm_commands import read_command
from .execution_errors import Inaccessible, OperationBlocked, TransientRead

PAGE_SIZE = 10
RECEIPT_PREFIX = "Archive Desk: "
POST_URL = re.compile(
    r"https://(?:www\.)?instagram\.com/(p|reels?)/([A-Za-z0-9_-]{1,32})/?(?:\?[^\s]*)?"
)
REEL_CODE = re.compile(r"[A-Za-z0-9_-]{1,32}")
REELS_AUDIO = re.compile(r"/reels/audio/([1-9][0-9]{0,31})/?")


@dataclass(frozen=True)
class SharedAudio:
    id: str
    title: str
    artist: str


@dataclass(frozen=True)
class DmMessage:
    id: str
    sender_id: str | None
    timestamp: datetime
    media_ref: str | None
    caption: str | None
    command: tuple[str, str | list[int]] | None = None
    client_context: str | None = None
    audio: tuple[SharedAudio, ...] = ()


def caption_instruction(item):
    command = read_command(item)
    return command[1] if command and command[0] == "caption" else None


def identity(value):
    raw = str(value)
    return raw if re.fullmatch(r"[0-9]{1,64}", raw) else None


def media_reference(item):
    kind = item.get("item_type")
    if kind in {"media_share", "clip", "clips_share"}:
        media = item.get(kind) or {}
        if isinstance(media, dict) and isinstance(media.get("clip"), dict):
            media = media["clip"]
        if isinstance(media, dict):
            code = media.get("code")
            if (
                (kind != "media_share" or media.get("product_type") == "clips")
                and isinstance(code, str)
                and REEL_CODE.fullmatch(code)
            ):
                return f"https://www.instagram.com/reel/{code}/"
            return identity(media.get("pk") or str(media.get("id", "")).split("_", 1)[0])
    if kind in {"text", "link", "xma_share", "xma_media_share", "xma_clip"}:
        text = item.get("text")
        link = item.get("link")
        link = link.get("link_context") if isinstance(link, dict) else None
        shares = item.get(kind)
        share = shares[0] if isinstance(shares, list) and shares else shares
        urls = [
            text.strip() if isinstance(text, str) else None,
            link.get("link_url") if isinstance(link, dict) else None,
            share.get("target_url") if isinstance(share, dict) else None,
        ]
        for url in urls:
            if isinstance(url, str) and (match := POST_URL.fullmatch(url)):
                path = "p" if match[1] == "p" else "reel"
                return f"https://www.instagram.com/{path}/{match[2]}/"
    return None


def audio_shares(item):
    if item.get("item_type") != "reels_audio_share":
        return ()
    entries = item.get("reels_audio_share")
    if not isinstance(entries, list):
        return ()
    tracks = {}
    for entry in entries[:4]:
        target = entry.get("target_url") if isinstance(entry, dict) else None
        if not isinstance(target, str) or len(target) > 1024:
            continue
        try:
            url = urlsplit(target)
            if url.scheme != "https" or url.netloc not in {"www.instagram.com", "instagram.com"}:
                continue
            if match := REELS_AUDIO.fullmatch(url.path):
                audio_id = match[1]
            elif url.path == "/_n/reels_audio_page":
                values = parse_qs(url.query, max_num_fields=12).get("audio_id") or []
                audio_id = values[0] if len(values) == 1 else ""
            else:
                continue
        except ValueError:
            continue
        if not re.fullmatch(r"[1-9][0-9]{0,31}", audio_id):
            continue
        title = entry.get("header_title_text") or entry.get("title_text")
        subtitle = entry.get("header_subtitle_text") or ""
        artist = subtitle.split("·", 1)[0].strip()[:250] if isinstance(subtitle, str) else ""
        if isinstance(title, str) and title.strip():
            tracks[audio_id] = SharedAudio(audio_id, title.strip()[:250], artist)
    return tuple(tracks.values())


def self_thread_id(client, actor):
    # The participant lookup used for two accounts does not resolve a self chat.
    # Ask for a single inbox snapshot and inspect only participant/author IDs.
    result = client.private_request(
        "direct_v2/inbox/",
        params={
            **client._direct_request_tracking_params(),
            "limit": "20",
            "thread_message_limit": "1",
            "visual_message_return_type": "unseen",
            "is_prefetching": "false",
            "fetch_reason": "initial_snapshot",
        },
    )
    threads = (result.get("inbox") or {}).get("threads")
    if result.get("status") != "ok" or not isinstance(threads, list) or len(threads) > 25:
        raise TransientRead("Instagram did not return a bounded inbox snapshot")
    matches = set()
    for thread in threads:
        if not isinstance(thread, dict) or thread.get("is_group") is not False:
            continue
        users, items = thread.get("users"), thread.get("items")
        if not isinstance(users, list) or any(
            not isinstance(user, dict) or identity(user.get("pk")) != actor for user in users
        ):
            continue
        if (
            not isinstance(items, list)
            or not items
            or any(
                not isinstance(item, dict) or identity(item.get("user_id")) != actor
                for item in items
            )
        ):
            continue
        if thread_id := identity(thread.get("thread_v2_id") or thread.get("thread_id")):
            matches.add(thread_id)
    if len(matches) > 1:
        raise TransientRead("Instagram returned multiple possible self conversations")
    return next(iter(matches), None)


class MissingDmConversation(Inaccessible):
    code = "DM_CONVERSATION_MISSING"


def read_page(client, sender_id, account_id, thread_id, cursor):
    if thread_id is None and sender_id == account_id:
        thread_id = self_thread_id(client, account_id)
        if thread_id is None:
            raise MissingDmConversation("No self conversation found in the recent inbox")
    if thread_id is None:
        try:
            result = client.direct_thread_by_participants([sender_id])
        except Inaccessible:
            raise MissingDmConversation(
                "No visible conversation with the approved sender"
            ) from None
        if not isinstance(result, dict):
            raise TransientRead("Instagram did not return a usable conversation lookup")
        target = result.get("thread", result)
        thread_id = (
            identity(target.get("thread_v2_id") or target.get("thread_id"))
            if isinstance(target, dict)
            else None
        )
        if not thread_id:
            if result.get("status") == "ok":
                raise MissingDmConversation("No visible conversation with the approved sender")
            raise TransientRead("Instagram did not return a usable conversation identity")
    params = {"direction": "older", "limit": str(PAGE_SIZE)}
    if cursor:
        params["cursor"] = cursor
    response = client.private_request(f"direct_v2/threads/{thread_id}/", params=params)
    thread = response.get("thread") or {}
    returned_ids = {identity(thread.get("thread_id")), identity(thread.get("thread_v2_id"))}
    if thread_id not in returned_ids or thread.get("is_group") is True:
        raise OperationBlocked("Only the approved direct conversation can be read")
    users = thread.get("users")
    if not isinstance(users, list) or {
        identity(user.get("pk")) for user in users if isinstance(user, dict)
    } - {account_id, sender_id}:
        raise OperationBlocked("The direct conversation participants could not be verified")
    if sender_id != account_id and sender_id not in {
        identity(user.get("pk")) for user in users if isinstance(user, dict)
    }:
        raise OperationBlocked("The approved sender is missing from this conversation")
    items = thread.get("items")
    next_cursor = thread.get("oldest_cursor") if thread.get("has_older", True) else None
    if (
        not isinstance(items, list)
        or len(items) > 25
        or (
            next_cursor
            and (
                not isinstance(next_cursor, str)
                or len(next_cursor) > 512
                or next_cursor == cursor
                or not items
            )
        )
    ):
        raise TransientRead("Instagram did not return a usable bounded direct message page")
    result = []
    for item in items:
        if not isinstance(item, dict) or not (message_id := identity(item.get("item_id"))):
            raise TransientRead("A direct message did not have a stable item ID")
        try:
            timestamp = datetime.fromtimestamp(int(item["timestamp"]) / 1_000_000, UTC)
        except (KeyError, ValueError, TypeError, OverflowError, OSError):
            raise TransientRead("A direct message did not have a usable timestamp") from None
        if result and timestamp > result[-1].timestamp:
            raise TransientRead("Direct message timestamps were not in the expected order")
        author = identity(item.get("user_id"))
        link = item.get("link")
        if author == account_id and any(
            isinstance(value, str) and value.startswith(RECEIPT_PREFIX)
            for value in (item.get("text"), link.get("text") if isinstance(link, dict) else None)
        ):
            author = None
        command = read_command(item) if author == sender_id else None
        result.append(
            DmMessage(
                message_id,
                author,
                timestamp,
                media_reference(item) if author == sender_id else None,
                command[1] if command and command[0] == "caption" else None,
                command,
                identity(item.get("client_context")),
                audio_shares(item) if author == sender_id else (),
            )
        )
    return thread_id, next_cursor or None, result


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class UnsupportedDmMedia(Inaccessible):
    code = "DM_MEDIA_UNSUPPORTED"


class SharedReel(Exception):
    def __init__(self, url, caption, media_id=None, owner_id=None, video_url=None):
        self.url = url
        self.caption = caption
        self.media_id = media_id
        self.owner_id = owner_id
        self.video_url = video_url


def get_shared_media(client, reference):
    media_id = identity(reference)
    if not media_id:
        if not POST_URL.fullmatch(reference):
            raise OperationBlocked("This message does not link a supported Instagram post")
        media_id = client.media_pk_from_url(reference)
    return client.media_info_v1(media_id)


def read_photo(client, reference, guard):
    media = get_shared_media(client, reference)
    if media.media_type != 1:
        raise UnsupportedDmMedia("This share contains multiple photos or video")
    if not media.thumbnail_url:
        raise Inaccessible("This single photo did not include an available original")
    track = observed_track(client, media.id)
    return download_photo(media.thumbnail_url, guard), (media.caption_text or "")[:2200], track


def read_shared_photos(client, reference, guard):
    media = get_shared_media(client, reference)
    match = POST_URL.fullmatch(reference)
    if media.media_type == 2 and (
        getattr(media, "product_type", None) == "clips" or (match and match[1] != "p")
    ):
        code = getattr(media, "code", None)
        if not isinstance(code, str) or not REEL_CODE.fullmatch(code):
            raise TransientRead("The Reel did not have a usable source link")
        raise SharedReel(
            f"https://www.instagram.com/reel/{code}/",
            (media.caption_text or "")[:2200],
            identity(getattr(media, "pk", None)),
            identity(getattr(getattr(media, "user", None), "pk", None)),
            getattr(media, "video_url", None),
        )
    if media.media_type == 1:
        photos = [media]
        skipped_videos = 0
    elif media.media_type == 8:
        photos = [item for item in media.resources if item.media_type == 1]
        skipped_videos = len(media.resources) - len(photos)
    else:
        raise UnsupportedDmMedia("This share contains video only")
    if not photos or len(photos) > MAX_CAROUSEL_PHOTOS:
        raise UnsupportedDmMedia("This share has no photos or exceeds the photo limit")
    if any(not photo.thumbnail_url for photo in photos):
        raise Inaccessible("An original in this share is unavailable")
    if len(photos) > 1:
        guard.reserve_carousel_downloads(len(photos) - 1)
    return (
        (download_photo(photo.thumbnail_url, guard) for photo in photos),
        (media.caption_text or "")[:2200],
        skipped_videos,
        observed_source_music(client, getattr(media, "id", None)),
    )


def download_photo(reference, guard):
    return download_media(reference, guard, {"image/jpeg", "image/png"}, MAX_BYTES)


def download_reel(reference, guard):
    from .content_video import MAX_VIDEO_BYTES

    return download_media(
        reference, guard, {"video/mp4", "application/octet-stream"}, MAX_VIDEO_BYTES
    )


def download_media(reference, guard, types, limit):
    url = str(reference)
    target = urlsplit(url)
    if (
        target.scheme != "https"
        or target.username
        or target.password
        or target.port not in {None, 443}
        or not (target.hostname or "").endswith((".cdninstagram.com", ".fbcdn.net"))
    ):
        raise OperationBlocked("The source did not resolve to an Instagram media host")
    guard.before_request()
    try:
        with build_opener(ProxyHandler({}), NoRedirect()).open(Request(url), timeout=10) as reply:
            if reply.headers.get_content_type() not in types:
                raise Inaccessible("Instagram did not return a supported media format")
            data = reply.read(limit + 1)
    except (URLError, OSError):
        raise Inaccessible("The shared media is unavailable to this account") from None
    return data
