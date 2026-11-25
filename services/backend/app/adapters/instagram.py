"""Domain results around the guarded, pinned backend-only Instagram client."""

import json
import re
from dataclasses import dataclass
from typing import Literal, Protocol

from ..catalog_music import observed_feed_music, observed_track
from ..execution_errors import OperationBlocked, TransientRead


@dataclass(frozen=True)
class RelationshipSnapshot:
    state: Literal["none", "following", "requested", "unknown"]
    follows_back: bool | None = None


class InstagramAdapter(Protocol):
    def relationship(self, target_id: str) -> RelationshipSnapshot: ...
    def follow(self, target_id: str) -> RelationshipSnapshot: ...
    def unfollow(self, target_id: str) -> RelationshipSnapshot: ...


@dataclass(frozen=True)
class DiscoveredUser:
    id: str
    username: str


@dataclass(frozen=True)
class CollectionPage:
    users: tuple[DiscoveredUser, ...]
    next_cursor: str | None
    complete: bool
    note: str | None = None


class LiveInstagramAdapter:
    def __init__(self, client, persist=None):
        self.client, self.persist = client, persist

    @staticmethod
    def identity(value):
        value = str(value)
        if not value.isdecimal() or len(value) > 64:
            raise OperationBlocked("Expected a real stable Instagram ID")
        return value

    def relationship(self, target_id):
        target_id = self.identity(target_id)
        result = self.client.private_request(
            f"friendships/show/{target_id}/", params={"is_external_deeplink_profile_view": "false"}
        )
        if (
            not isinstance(result, dict)
            or type(result.get("following")) is not bool
            or type(result.get("outgoing_request")) is not bool
        ):
            raise TransientRead("Relationship response is incomplete")
        state = (
            "following"
            if result["following"]
            else "requested"
            if result["outgoing_request"]
            else "none"
        )
        follows_back = result.get("followed_by")
        return RelationshipSnapshot(state, follows_back if type(follows_back) is bool else None)

    def follow(self, target_id):
        target_id = self.identity(target_id)
        self.client._users_following.get(str(self.client.user_id), {}).pop(target_id, None)
        self.client.user_follow(target_id)
        return self.relationship(target_id)

    def unfollow(self, target_id):
        target_id = self.identity(target_id)
        self.client.user_unfollow(target_id)
        return self.relationship(target_id)

    def resolve_user(self, username):
        result = self.client.user_info_by_username_v1(username)
        return DiscoveredUser(self.identity(result.pk), result.username)

    def inspect(self, kind, target, limit, cursor=None, known_user_id=None):
        from ..insight_reader import read

        return read(self, kind, target, limit, cursor, known_user_id)

    def feed_music_config(self, track_id, playlist_music=None):
        if playlist_music:
            try:
                params = self.client._feed_music_params(
                    {
                        "audio_asset_id": playlist_music["audio_asset_id"],
                        "audio_cluster_id": playlist_music["audio_cluster_id"],
                        "title": playlist_music["title"],
                        "display_artist": playlist_music["artist"],
                    },
                    audio_asset_start_time=playlist_music["start_ms"],
                )
            except AssertionError:
                raise OperationBlocked("Instagram feed music is unavailable") from None
            return {"music_params": params}
        if not track_id:
            return {}
        track = self.client.track_info_by_canonical_id(track_id)
        if track.music_canonical_id and str(track.music_canonical_id) != track_id:
            raise OperationBlocked("Instagram resolved a different music track")
        try:
            music = self.client._feed_music_params(track)
        except AssertionError:
            raise OperationBlocked("This Instagram track is unavailable for feed posts") from None
        return {"music_params": {**music, "music_canonical_id": track_id}}

    def publish_photo(self, path, upload_id, values):
        # The SDK convenience upload wrappers retry configure and infer recent
        # media. This application deliberately calls each low-level step once.
        extra = self.feed_music_config(
            values.get("music_canonical_id"), values.get("playlist_music")
        )
        options = {"extra_data": extra} if extra else {}
        uploaded, width, height = self.client.photo_rupload(
            path, upload_id=upload_id, for_story=values["kind"] == "story_photo"
        )
        if str(uploaded) != upload_id:
            raise OperationBlocked("Upload receipt does not match the approved intent")
        configure = (
            self.client.photo_configure_to_story
            if values["kind"] == "story_photo"
            else self.client.photo_configure
        )
        result = configure(upload_id, width, height, values["caption"], **options)
        return self.publication_receipt(result)

    def publish_carousel(self, paths, upload_id, child_upload_ids, values):
        extra = self.feed_music_config(
            values.get("music_canonical_id"), values.get("playlist_music")
        )
        children = []
        for path, child_id in zip(paths, child_upload_ids, strict=True):
            uploaded, width, height = self.client.photo_rupload(
                path, upload_id=child_id, to_album=True
            )
            if str(uploaded) != child_id:
                raise OperationBlocked("Upload receipt does not match the approved carousel photo")
            children.append(
                {
                    "upload_id": child_id,
                    "edits": json.dumps(
                        {
                            "crop_original_size": [width, height],
                            "crop_center": [0.0, 0.0],
                            "crop_zoom": 1.0,
                        }
                    ),
                    "extra": json.dumps({"source_width": width, "source_height": height}),
                    "scene_capture_type": "",
                    "scene_type": None,
                }
            )
        result = self.client.album_configure(
            children,
            values["caption"],
            extra_data={"upload_id": upload_id, "client_sidecar_id": upload_id, **extra},
        )
        return self.publication_receipt(result)

    def publish_reel(self, path, thumbnail, upload_id, info, caption):
        """Use one settings request, one video upload and one configure for the frozen draft."""
        from ..content_video import reel_session_id

        video = path.read_bytes()
        width, height = info["width"], info["height"]
        duration_ms = round(info["duration"] * 1000)
        session_id = reel_session_id(self.client.user_id, upload_id)
        settings_url = f"https://i.instagram.com/upload_settings/{session_id}"
        body = {
            "composer_session_id": session_id,
            "upload_setting_properties": {
                "upload_settings_version": "v0.1",
                "codec": {},
                "context": {"source_type": "clips", "target_id": int(self.client.user_id)},
                "video": {
                    "video_height": height,
                    "video_width": width,
                    "video_original_file_size": len(video),
                    "video_duration_milliseconds": duration_ms,
                    "target_duration": int(info["duration"]),
                },
                "creative_tools": {"transmuxing_eligible": False, "transcoding_required": True},
            },
            "preview_spec": {
                "spec_version": 1,
                "video_dur_ms": duration_ms,
                "audio_dur_ms": duration_ms,
            },
        }
        settings_headers = self.client.private_headers(
            {
                "X-Entity-Name": "upload_settings",
                "X-Entity-Type": "application/json",
                "Content-Type": "application/json",
                "Offset": "0",
                "X_FB_VIDEO_WATERFALL_ID": f"{session_id}_settings",
            }
        )
        reply = self.client.private.post(settings_url, json=body, headers=settings_headers)
        if reply.status_code != 200:
            raise OperationBlocked("Instagram did not accept the Reel upload settings")
        name = f"{session_id.replace('-', '')}-0-{len(video)}-{upload_id}-{upload_id}"
        url = f"https://i.instagram.com/rupload_igvideo/{name}"
        headers = self.client.private_headers(
            {
                "X-Instagram-Rupload-Params": json.dumps(
                    {
                        "upload_id": upload_id,
                        "session_id": upload_id,
                        "media_type": "2",
                        "share_type": "reels",
                        "is_clips_video": "1",
                        "xsharing_user_ids": "[]",
                        "upload_media_duration_ms": str(duration_ms),
                        "upload_media_width": str(width),
                        "upload_media_height": str(height),
                        "extract_cover_frame": "1",
                        "content_tags": "use_default_cover",
                    }
                ),
                "X_FB_VIDEO_WATERFALL_ID": f"{session_id}_Mixed_0",
                "X-Entity-Type": "video/mp4",
                "Segment-Start-Offset": "0",
                "Segment-Type": "3",
            }
        )
        reply = self.client.private.get(url, headers=headers)
        if reply.status_code != 200:
            raise OperationBlocked("Instagram did not initialize the Reel upload")
        reply = self.client.private.post(
            url,
            data=video,
            headers={
                **headers,
                "Offset": "0",
                "X-Entity-Name": name,
                "X-Entity-Length": str(len(video)),
                "Content-Type": "application/octet-stream",
            },
        )
        if reply.status_code != 200:
            raise OperationBlocked("Instagram did not accept the Reel video")
        result = self.client.clip_configure(
            upload_id, thumbnail, width, height, info["duration"], caption
        )
        return self.publication_receipt(result)

    @staticmethod
    def publication_receipt(result):
        media = result.get("media") if isinstance(result, dict) else None
        media_id = str(media.get("id", "")) if isinstance(media, dict) else ""
        if (
            not isinstance(result, dict)
            or result.get("status") != "ok"
            or not re.fullmatch(r"[0-9]{1,32}(?:_[0-9]{1,32})?", media_id)
        ):
            from ..execution_errors import UncertainWrite

            raise UncertainWrite("Instagram did not return a confirmed publication ID")
        receipt = {"media_id": media_id}
        items = media.get("carousel_media")
        if isinstance(items, list) and items:
            ids = [
                str(item.get("pk", item.get("id", ""))).split("_", 1)[0]
                for item in items
                if isinstance(item, dict)
            ]
            if len(ids) == len(items) and all(re.fullmatch(r"[0-9]{1,32}", value) for value in ids):
                receipt["child_media_ids"] = ids
        return receipt

    def resolve_media_ref(self, reference):
        if reference.startswith("https://"):
            from urllib.parse import urlsplit

            path = urlsplit(reference).path.strip("/").split("/")
            return (
                path[2] if path[0] == "stories" else str(self.client.media_pk_from_url(reference))
            )
        return reference

    def owned_media(self, media_id, actor_id):
        media = self.client.media_info_v1(media_id)
        if str(media.user.pk) != str(actor_id):
            raise OperationBlocked("The selected media is not owned by the connected account")
        if str(media.id).split("_", 1)[0] != str(media_id).split("_", 1)[0]:
            raise OperationBlocked("The media observation does not identify the approved target")
        if media.media_type == 2 and media.product_type == "clips":
            return {
                "media_id": str(media.id),
                "caption": media.caption_text or "",
                "kind": "feed_reel",
                "media": media,
            }
        music = observed_track(self.client, media_id)
        feed_music = observed_feed_music(self.client, media_id)
        if media.media_type == 8 and media.product_type in {"feed", "carousel_container", ""}:
            if not media.resources or any(item.media_type != 1 for item in media.resources):
                raise OperationBlocked("This carousel does not contain only photos")
            return {
                "media_id": str(media.id),
                "caption": media.caption_text or "",
                "kind": "feed_carousel",
                "child_media_ids": [self.identity(item.pk) for item in media.resources],
                "media": media,
                "music_canonical_id": music["id"] if music else None,
                "playlist_music": feed_music,
            }
        if media.media_type != 1 or media.product_type not in {"feed", "story", ""}:
            raise OperationBlocked("Only supported own photo publications can be changed")
        return {
            "media_id": str(media.id),
            "caption": media.caption_text or "",
            "kind": "story_photo" if media.product_type == "story" else "feed_photo",
            "media": media,
            "music_canonical_id": music["id"] if music else None,
            "playlist_music": feed_music,
        }

    def edit_caption(self, media_id, caption, observation):
        # Preserve existing tags/location instead of the SDK's empty defaults.
        media = observation["media"]
        # Send the inspected SDK edit shape directly: its convenience helper
        # first calls a cache/public lookup that could add fallback requests.
        result = self.client.private_request(
            f"media/{media_id}/edit_media/",
            self.client.with_default_data(
                {
                    "caption_text": caption,
                    "container_module": "edit_media_info",
                    "feed_position": "0",
                    "location": self.client.location_build(media.location),
                    "usertags": json.dumps(
                        {
                            "in": [
                                {"user_id": tag.user.pk, "position": [tag.x, tag.y]}
                                for tag in media.usertags
                            ]
                        }
                    ),
                    "is_carousel_bumped_post": "false",
                }
            ),
        )
        if not isinstance(result, dict) or result.get("status") != "ok":
            from ..execution_errors import UncertainWrite

            raise UncertainWrite("Instagram did not confirm the caption update")

    def delete_story(self, media_id):
        self.remove_photo(media_id)

    def delete_post(self, media_id):
        self.remove_photo(media_id)

    def remove_photo(self, media_id):
        if self.client.media_delete(media_id) is not True:
            from ..execution_errors import UncertainWrite

            raise UncertainWrite("Instagram did not confirm media removal")

    def _users(self, values, limit):
        if not isinstance(values, list):
            raise TransientRead("Collection response has no validated user list")
        users = []
        for value in values[:limit]:
            if isinstance(value, dict):
                user_id, username = value.get("pk", value.get("pk_id")), value.get("username")
            else:
                user_id, username = value.pk, value.username
            if not isinstance(username, str) or not 1 <= len(username) <= 64:
                raise TransientRead("Collection response contains an invalid identity")
            users.append(DiscoveredUser(self.identity(user_id), username))
        return tuple(users)

    def collect_followers(self, user_id, cursor=None, amount=10, order="default", *, query=""):
        if not 1 <= amount <= 100:
            raise OperationBlocked("Collection size must be bounded to 1–100")
        params = {
            "count": min(max(amount, 1), 100),
            "rank_token": self.client.rank_token,
            "search_surface": "follow_list_page",
            "query": query,
            "enable_groups": "true",
        }
        if cursor:
            params["max_id"] = cursor
        if order in {"newest", "oldest"}:
            params["order"] = (
                "date_followed_latest" if order == "newest" else "date_followed_earliest"
            )
        result = self.client.private_request(
            f"friendships/{self.identity(user_id)}/followers/", params=params
        )
        if not isinstance(result, dict) or result.get("status") != "ok":
            raise TransientRead("Collection response was not successful")
        next_cursor = result.get("next_max_id") or None
        if next_cursor is not None and (
            not isinstance(next_cursor, str) or len(next_cursor) > 200 or next_cursor == cursor
        ):
            raise TransientRead("Collection cursor did not advance")
        users = result.get("users")
        truncated = isinstance(users, list) and len(users) > amount
        return CollectionPage(
            self._users(users, amount),
            next_cursor,
            next_cursor is None and not truncated,
            "Response exceeded the requested batch; only a bounded sample was retained."
            if truncated
            else None,
        )

    def collect_mutuals(self, user_id, cursor, amount):
        from ..schemas import MUTUAL_BATCH_LIMIT

        if not 1 <= amount <= MUTUAL_BATCH_LIMIT:
            raise OperationBlocked("Mutual collection size is outside the supported range")
        following = self.collect_following(user_id, cursor, amount)
        mutuals = tuple(
            user
            for user in {user.id: user for user in following.users}.values()
            if any(
                follower.id == user.id
                for follower in self.collect_followers(
                    user_id, amount=10, query=user.username
                ).users
            )
        )
        return CollectionPage(
            mutuals,
            following.next_cursor,
            following.complete,
            f"Verified {len(mutuals)} of {len(following.users)} sampled following accounts "
            "in both lists. A missing match is inconclusive.",
        )

    def collect_likers(self, url, amount=10):
        if not 1 <= amount <= 100:
            raise OperationBlocked("Collection size must be bounded to 1–100")
        media_pk = self.client.media_pk_from_url(url)
        users = self.client.media_likers(media_pk)
        return CollectionPage(
            self._users(users, amount),
            None,
            False,
            "Instagram returned one liker response; completeness is unavailable.",
        )

    def collect_following(self, user_id, cursor=None, amount=10):
        if not 1 <= amount <= 100:
            raise OperationBlocked("Collection size must be bounded to 1–100")
        params = {"count": min(max(amount, 1), 100), "rank_token": self.client.rank_token}
        if cursor:
            params["max_id"] = cursor
        result = self.client.private_request(
            f"friendships/{self.identity(user_id)}/following/", params=params
        )
        if not isinstance(result, dict) or result.get("status") != "ok":
            raise TransientRead("Following import response was not successful")
        next_cursor = result.get("next_max_id") or None
        if next_cursor and (
            not isinstance(next_cursor, str) or len(next_cursor) > 200 or next_cursor == cursor
        ):
            raise TransientRead("Following import cursor did not advance")
        users = result.get("users")
        truncated = isinstance(users, list) and len(users) > amount
        return CollectionPage(
            self._users(users, amount),
            next_cursor,
            next_cursor is None and not truncated,
            "Following response exceeded the requested batch; import remains partial."
            if truncated
            else None,
        )
