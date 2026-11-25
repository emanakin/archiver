"""Exercise actual pinned upload/configure/edit/delete helpers with networking disabled."""

import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs

import requests
from PIL import Image

from app.adapters.guarded_client import build_client
from app.adapters.instagram import LiveInstagramAdapter
from app.execution_errors import OperationBlocked, UncertainWrite


class Guard:
    allow_auth = False
    allow_media = True
    fatal_error = None

    def __init__(self):
        self.events = []

    def before_request(self, **metadata):
        self.events.append(metadata)


def response(session, request, **kwargs):
    assert kwargs["allow_redirects"] is False
    result = requests.Response()
    result.status_code, result.url, result.request = 200, request.url, request
    if "/music/search_v2/" in request.url:
        assert parse_qs(request.body)["q"] == ["Fixture track Fixture artist"]
        value = {"status": "ok", "items": []}
    elif "/music/music_in_feed_audio_browser/" in request.url:
        value = {"status": "ok", "alacorn_session_id": "fixture"}
    elif "/delete/" in request.url:
        value = {"status": "ok", "did_delete": True}
    elif "/configure_sidecar/" in request.url:
        value = {"status": "ok", "media": {"id": "987_999", "carousel_media": [{"pk": "91"}, {"pk": "92"}]}}
    else:
        value = {"status": "ok", "media": {"id": "987_999"}}
    if "/edit_media/" in request.url:
        form = parse_qs(request.body)
        values = json.loads(form["signed_body"][0].split(".", 1)[1])
        assert json.loads(values["usertags"])["in"] == [{"user_id": "111", "position": [0.2, 0.3]}]
    result._content = json.dumps(value).encode()
    return result


with tempfile.TemporaryDirectory() as directory:
    path = Path(directory) / "synthetic.jpg"
    for kind in ("feed_photo", "story_photo"):
        Image.new(
            "RGB", (1080, 1920) if kind == "story_photo" else (1080, 1350), (30, 90, 60)
        ).save(path)
        guard = Guard()
        with patch("requests.sessions.Session.send", response):
            client = build_client(guard, {"authorization_data": {"ds_user_id": "999"}})
            adapter = LiveInstagramAdapter(client)
            receipt = adapter.publish_photo(
                path, "1795040000000", {"kind": kind, "caption": "Reviewed caption"}
            )
            assert receipt["media_id"] == "987_999"
            assert [event["media"]["operation"] for event in guard.events] == [
                "upload",
                "configure_story" if kind == "story_photo" else "configure_photo",
            ]
            assert guard.events[-1]["media"]["caption"] == "Reviewed caption"
            if kind == "feed_photo":
                media = SimpleNamespace(
                    usertags=[SimpleNamespace(user=SimpleNamespace(pk="111"), x=0.2, y=0.3)],
                    location=None,
                )
                adapter.edit_caption("987_999", "New caption", {"media": media})
            else:
                adapter.delete_story("987_999")
            assert guard.events[-1]["media"]["operation"] == (
                "edit_caption" if kind == "feed_photo" else "delete_story"
            )
            if kind == "feed_photo":
                guard.media_removal_kind = "delete_post"
                adapter.delete_post("987_999")
                assert guard.events[-1]["media"]["operation"] == "delete_post"
                assert guard.events[-1]["media"]["media_id"] == "987_999"
    # A failed configure must return unknown after exactly one call. Never use
    # the SDK convenience wrapper's retry/recent-media fallback.
    guard = Guard()
    writes = []
    with patch("requests.sessions.Session.send", response):
        client = build_client(guard, {"authorization_data": {"ds_user_id": "999"}})

        def timeout(*args, **kwargs):
            writes.append(True)
            raise UncertainWrite("Synthetic configure timeout")

        with patch.object(client, "photo_configure", side_effect=timeout):
            try:
                LiveInstagramAdapter(client).publish_photo(
                    path, "1795040000001", {"kind": "feed_photo", "caption": "Reviewed"}
                )
                raise AssertionError("Expected unknown configure outcome")
            except UncertainWrite:
                pass
    assert len(writes) == 1
    guard = Guard()
    guard.approved_music = "123456"
    with patch("requests.sessions.Session.send", response):
        client = build_client(guard, {"authorization_data": {"ds_user_id": "999"}})
        adapter = LiveInstagramAdapter(client)
        track = SimpleNamespace(
            audio_asset_id="234567",
            audio_cluster_id="345678",
            music_canonical_id="123456",
            alacorn_session_id="fixture",
            highlight_start_times_in_ms=[0],
            display_artist="Fixture artist",
            title="Fixture track",
        )
        with patch.object(client, "track_info_by_canonical_id", return_value=track):
            receipt = adapter.publish_photo(
                path, "1795040000002",
                {"kind": "feed_photo", "caption": "With music", "music_canonical_id": "123456"},
            )
        assert receipt["media_id"] == "987_999"
        assert guard.events[-1]["media"]["music_canonical_id"] == "123456"
    with Image.new("RGB", (640, 480), (90, 40, 20)) as original_frame:
        original_frame.save(path)
    guard = Guard()
    guard.approved_music = "123456"
    with patch("requests.sessions.Session.send", response):
        client = build_client(guard, {"authorization_data": {"ds_user_id": "999"}})
        adapter = LiveInstagramAdapter(client)
        with patch.object(client, "track_info_by_canonical_id", return_value=track):
            receipt = adapter.publish_carousel(
                [path, path], "1795040000003", ["1795040000004", "1795040000005"],
                {"kind": "feed_carousel", "caption": "Ordered carousel", "music_canonical_id": "123456"},
            )
    assert receipt["child_media_ids"] == ["91", "92"]
    assert [event["media"]["operation"] for event in guard.events] == ["upload", "upload", "configure_carousel"]
    assert guard.events[-1]["media"]["child_upload_ids"] == ["1795040000004", "1795040000005"]
    assert guard.events[-1]["media"]["music_canonical_id"] == "123456"
    guard = Guard()
    guard.music_queries = {"Fixture track Fixture artist"}
    guard.feed_music = {
        "audio_asset_id": "234567",
        "audio_cluster_id": "345678",
        "title": "Fixture track",
        "artist": "Fixture artist",
        "start_ms": 1000,
    }
    with patch("requests.sessions.Session.send", response):
        client = build_client(guard, {"authorization_data": {"ds_user_id": "999"}})
        assert client.music_search_v2("Fixture track Fixture artist")["status"] == "ok"
        adapter = LiveInstagramAdapter(client)
        adapter.publish_photo(
            path, "1795040000006",
            {"kind": "feed_photo", "caption": "With playlist music", "playlist_music": guard.feed_music},
        )
        assert guard.events[-1]["media"]["playlist_music"] == guard.feed_music
        assert guard.events[-1]["media"]["music_canonical_id"] is None
        adapter.publish_carousel(
            [path, path], "1795040000007", ["1795040000008", "1795040000009"],
            {"kind": "feed_carousel", "caption": "With music", "playlist_music": guard.feed_music},
        )
        assert guard.events[-1]["media"]["playlist_music"] == guard.feed_music
        try:
            client.music_search_v2("Unapproved search")
        except OperationBlocked:
            pass
        else:
            raise AssertionError("The transport must reject searches absent from the approved batch")
print(
    "PASS: pinned photo, story and carousel uploads, feed music, caption tags and removal; zero network requests"
)
