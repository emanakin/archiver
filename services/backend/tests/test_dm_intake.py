import io
import os
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import (
    caption_writer,
    content_drafts,
    content_publication,
    dm_intake,
    dm_reader,
    dm_receipts,
    saved_audio,
    spotify_playlist,
)
from app.caption_writer import normalize_caption
from app.config import settings
from app.content_drafts import DraftPatch, StoryDraftCreate, create_story_draft, edit_draft
from app.content_images import validate_image
from app.content_storage import REMOTE_BACKENDS, bucket_args, read_original, upload_backend
from app.dm_commands import read_command
from app.dm_reader import DmMessage, caption_instruction, media_reference, read_page
from app.execution_errors import OperationBlocked, UncertainWrite
from app.executor import Executor, aware
from app.models import (
    Account,
    AccountSettings,
    BudgetUsage,
    ContentAsset,
    ContentDraft,
    DmInbox,
    DmSubmission,
    InsightCheck,
    Job,
    PlaylistSelection,
    PlaylistTrack,
    SavedAudio,
    now,
)
from app.music_context import named_song, named_track
from app.schemas import SettingsValues
from app.spotify_playlist import available_tracks, match_track, playlist_candidates, song_key
from app.timing import local_day


def test_dm_media_reference_is_normalized_without_fetching_links():
    assert media_reference({"item_type": "media_share", "media_share": {"id": "123_456"}}) == "123"
    assert (
        media_reference(
            {"item_type": "text", "text": "https://instagram.com/p/Abc_1/?igsh=private"}
        )
        == "https://www.instagram.com/p/Abc_1/"
    )
    assert (
        media_reference(
            {"item_type": "text", "text": "https://instagram.com.other.example/p/Abc_1/"}
        )
        is None
    )


def test_group_conversation_is_rejected():
    client = SimpleNamespace(
        private_request=lambda *_, **__: {
            "thread": {"thread_id": "1", "is_group": True, "users": [{"pk": "2"}]}
        }
    )
    with pytest.raises(OperationBlocked):
        read_page(client, "2", "3", "1", None)


def test_missing_dm_thread_is_distinct_from_an_empty_existing_conversation():
    client = SimpleNamespace(
        direct_thread_by_participants=lambda _: {"status": "ok", "thread": None}
    )
    with pytest.raises(dm_reader.MissingDmConversation):
        read_page(client, "2", "3", None, None)
    client.private_request = lambda *_, **__: {
        "thread": {"thread_id": "1", "users": [{"pk": "2"}], "items": [], "has_older": False}
    }
    assert read_page(client, "2", "3", "1", None) == ("1", None, [])


def test_reel_links_and_native_clip_shares_normalize():
    link = {
        "item_type": "xma_clip",
        "xma_clip": [{"target_url": "https://instagram.com/reels/Clip_1?igsh=private"}],
    }
    clip = {"item_type": "clip", "clip": {"clip": {"code": "Clip_1", "pk": "123"}}}
    assert (
        media_reference(link) == media_reference(clip) == "https://www.instagram.com/reel/Clip_1/"
    )


def test_caption_tags_follow_source_topics_and_skip_generic_tags():
    caption = "that jazz collection is doing all the talking."
    source = "This runway meme pairs jazz with streetwear. #Jazz #runway #viral #Reel #cooking"
    assert caption_writer.add_caption_tags(caption, source) == caption + "\n\n#meme #jazz #runway"
    assert caption_writer.add_caption_tags("a quiet day.", "#viral #reels") == "a quiet day."
    assert caption_writer.add_caption_tags("when the plan changes.", "", "This is a meme.") == (
        "when the plan changes.\n\n#meme"
    )


@pytest.mark.skipif(not os.getenv("TEST_POSTGRES_URL"), reason="Requires PostgreSQL account lane")
def test_reel_share_prepares_caption_without_queuing_publication(postgres, monkeypatch):
    engine, aid = postgres
    config = settings().model_copy(update={"adapter_mode": "live", "dm_caption_model": "test"})
    monkeypatch.setattr(dm_intake, "settings", lambda: config)
    monkeypatch.setattr(
        dm_intake, "write_caption", lambda *_: "the tailoring is part of the punchline."
    )
    at = now()
    ref = "https://www.instagram.com/reel/Clip_1/"
    with Session(engine) as db, db.begin():
        account = db.get(Account, aid)
        account.mode, account.session_state, account.instagram_id = "live", "connected", "42"
        db.add(
            AccountSettings(
                account_id=aid, values=SettingsValues(request_ceiling=None).model_dump()
            )
        )
        inbox = DmInbox(
            account_id=aid,
            sender_id="555",
            sender_username="source",
            direct_publish_from=at - timedelta(minutes=1),
            created_at=at - timedelta(minutes=1),
        )
        db.add(inbox)
        db.flush()
        dm_intake.record_page(db, inbox, "123", None, [DmMessage("10", "555", at, ref, None)], at)

    def adapter(guard):
        def info(_):
            guard.before_request()
            return SimpleNamespace(
                media_type=2,
                product_type="clips",
                code="Clip_1",
                caption_text="A tailoring meme from the runway. #runway #viral",
            )

        return SimpleNamespace(
            client=SimpleNamespace(media_info_v1=info, media_pk_from_url=lambda _: "9")
        )

    assert Executor(engine, adapter).run_account(aid)
    with Session(engine) as db:
        item = dm_intake.list_submissions(aid, 20, 0, db)["items"][0]
        assert item.state == "reel_ready" and item.reel_url == ref
        assert (
            item.reel_caption
            == "the tailoring is part of the punchline.\n\n#meme #runway #tailoring"
        )
        assert item.draft_id is None
        assert set(db.scalars(select(Job.kind))) == {"dm_import"}


def test_self_thread_keeps_commands_scoped_to_the_connected_account():
    stamp = str(int(now().timestamp() * 1_000_000))
    thread = {
        "thread_id": "1",
        "is_group": False,
        "users": [],
        "has_older": False,
        "items": [
            {
                "item_id": "5",
                "timestamp": stamp,
                "user_id": "2",
                "item_type": "text",
                "text": "^ draft",
            },
            {
                "item_id": "4",
                "timestamp": stamp,
                "user_id": "3",
                "item_type": "text",
                "text": "^ autopost",
            },
        ],
    }
    client = SimpleNamespace(private_request=lambda *_, **__: {"thread": thread})
    _, _, messages = read_page(client, "2", "2", "1", None)
    assert [message.command for message in messages] == [("publish", "draft"), None]
    thread["users"] = [{"pk": "3"}]
    with pytest.raises(OperationBlocked):
        read_page(client, "2", "2", "1", None)


def test_a_missing_self_conversation_is_reported():
    client = SimpleNamespace(
        _direct_request_tracking_params=lambda: {},
        private_request=lambda endpoint, params: {"status": "ok", "inbox": {"threads": []}},
    )
    with pytest.raises(dm_reader.MissingDmConversation):
        read_page(client, "2", "2", None, None)


def test_self_discovery_ignores_other_conversations_and_does_not_import_a_receipt():
    stamp = str(int(now().timestamp() * 1_000_000))
    candidate = {"thread_id": "1", "is_group": False, "users": [], "items": [{"user_id": "2"}]}
    known = "https://www.instagram.com/p/Test_A/"
    detail = {
        **candidate,
        "has_older": False,
        "items": [
            {
                "item_id": "4",
                "timestamp": stamp,
                "user_id": "2",
                "item_type": "link",
                "link": {
                    "text": "Archive Desk: Posted!\n" + known,
                    "link_context": {"link_url": known},
                },
            },
            {
                "item_id": "3",
                "timestamp": stamp,
                "user_id": "2",
                "item_type": "xma_media_share",
                "xma_media_share": [{"target_url": known + "?igsh=private"}],
            },
        ],
    }

    def request(endpoint, params):
        if endpoint == "direct_v2/inbox/":
            assert params["thread_message_limit"] == "1"
            return {
                "status": "ok",
                "inbox": {
                    "threads": [
                        {**candidate, "thread_id": "6", "users": [{"pk": "3"}]},
                        {**candidate, "thread_id": "7", "is_group": True},
                        candidate,
                    ]
                },
            }
        assert endpoint == "direct_v2/threads/1/"
        return {"thread": detail}

    client = SimpleNamespace(_direct_request_tracking_params=lambda: {}, private_request=request)
    thread, cursor, messages = read_page(client, "2", "2", None, None)
    assert thread == "1" and cursor is None
    assert messages[0].sender_id is None and messages[0].media_ref is None
    assert messages[1].media_ref == known


@pytest.mark.parametrize(
    ("hold", "caption", "outcome", "message"),
    [
        (None, None, "draft", "Saved as a draft."),
        (
            "playlist_unavailable",
            "generation_failed",
            "draft",
            "Saved as a draft. Caption generation failed. No unused playlist track matched in Instagram.",
        ),
        ("held_as_draft", None, "draft", "Saved as a draft."),
        (None, None, "published", "Posted!"),
        (None, None, "failed", "Could not publish this draft."),
    ],
)
def test_self_receipt_contains_outcome_and_authorizes_one_exact_dispatch(
    system, monkeypatch, hold, caption, outcome, message
):
    _, db, account, _ = system
    account.instagram_id = "22"
    config = settings().model_copy(
        update={"dm_self_receipts": True, "dm_default_account_id": account.id}
    )
    monkeypatch.setattr(dm_receipts, "settings", lambda: config)
    inbox = DmInbox(
        account_id=account.id,
        sender_id="22",
        sender_username=account.username,
        thread_id="1",
        direct_publish_from=now(),
    )
    draft = ContentDraft(
        account_id=account.id,
        asset_id="fixture",
        status=outcome,
        media_id="4036217963292165883_22" if outcome == "published" else None,
    )
    db.add_all([inbox, draft])
    db.flush()
    submission = DmSubmission(
        account_id=account.id,
        sender_id="22",
        thread_id="1",
        message_id="3",
        draft_id=draft.id,
        draft_commands={"status": hold},
        caption_command={"state": caption},
    )
    db.add(submission)
    db.flush()
    origin = Job(
        account_id=account.id,
        kind="dm_import" if outcome == "draft" else "publish_photo",
        idempotency_key="original-share",
        payload={"submission_id": submission.id, "draft_id": draft.id},
    )
    db.add(origin)
    db.flush()
    state = "failed" if outcome == "failed" else "succeeded"
    dm_receipts.queue_receipt(db, account, origin, state)
    dm_receipts.queue_receipt(db, account, origin, state)
    receipt = db.scalars(select(Job).where(Job.kind == "dm_receipt")).one()
    location = f"{config.app_origin}/content?account={account.id}&draft={draft.id}"
    if outcome == "published":
        location = "https://www.instagram.com/p/DgDhtpSFIb7/"
    assert receipt.payload["text"] == f"Archive Desk: {message}\n{location}"
    attempt = SimpleNamespace(id="fixture", dispatch_started_at=None)
    form = {
        "thread_ids": [1, 2],
        "text": receipt.payload["text"],
        "links": [location],
        "context": "12",
    }
    with pytest.raises(OperationBlocked):
        dm_receipts.authorize_receipt(db, account, receipt, attempt, form, now())
    form["thread_ids"] = [1]
    dm_receipts.authorize_receipt(db, account, receipt, attempt, form, now())
    with pytest.raises(UncertainWrite):
        dm_receipts.authorize_receipt(db, account, receipt, attempt, form, now())


def test_failed_share_receipt_links_to_public_queue(system, monkeypatch):
    _, db, account, _ = system
    account.instagram_id = "22"
    config = settings().model_copy(
        update={
            "app_origin": "https://myarchiver.net",
            "dm_self_receipts": True,
            "dm_default_account_id": account.id,
        }
    )
    monkeypatch.setattr(dm_receipts, "settings", lambda: config)
    db.add(
        DmInbox(
            account_id=account.id,
            sender_id="22",
            sender_username=account.username,
            thread_id="1",
            direct_publish_from=now(),
        )
    )
    submission = DmSubmission(account_id=account.id, sender_id="22", thread_id="1", message_id="3")
    db.add(submission)
    db.flush()
    origin = Job(
        account_id=account.id,
        kind="dm_import",
        idempotency_key="original-share",
        payload={"submission_id": submission.id},
    )
    db.add(origin)
    db.flush()

    dm_receipts.queue_receipt(db, account, origin, "failed")

    receipt = db.scalars(select(Job).where(Job.kind == "dm_receipt")).one()
    assert receipt.payload["text"].splitlines()[-1] == (
        f"https://myarchiver.net/queue?account={account.id}&tab=review"
    )


def test_replacement_music_updates_only_the_new_draft_before_captioning(system, monkeypatch):
    from types import SimpleNamespace

    _, db, account, _ = system
    account.instagram_id = "22"
    inbox = DmInbox(account_id=account.id, sender_id="22", sender_username=account.username)
    original = ContentDraft(
        account_id=account.id, asset_id="fixture", kind="feed_photo", status="published"
    )
    replacement = ContentDraft(account_id=account.id, asset_id="fixture", kind="feed_photo")
    db.add_all([inbox, original, replacement])
    db.flush()
    submission = DmSubmission(
        account_id=account.id,
        sender_id="22",
        thread_id="12",
        message_id="34",
        draft_id=original.id,
    )
    db.add(submission)
    db.flush()
    job = Job(
        account_id=account.id,
        kind="dm_replacement_music",
        state="running",
        claim_owner="owner",
        idempotency_key="prepare-replacement",
        payload={
            "sender_id": "22",
            "inbox_version": inbox.version,
            "replacement_from": original.id,
            "replacement_source_submission_id": submission.id,
            "replacement_draft_id": replacement.id,
            "replacement_revision": replacement.revision,
            "replacement_caption_source": "original caption context",
        },
    )
    db.add(job)
    db.flush()
    engine, job_id = db.get_bind(), job.id
    lane = SimpleNamespace(account_id=account.id, owner="owner", assert_owned=lambda: None)
    db.commit()
    monkeypatch.setattr(dm_intake, "music_for_share", lambda *args: ("37", None, None, []))

    dm_intake.perform(engine, lane, job_id, None, None, now)

    db.refresh(original)
    db.refresh(replacement)
    assert original.status == "published" and original.music_canonical_id is None
    assert replacement.music_canonical_id == "37" and replacement.revision == 2
    assert replacement.caption_review["state"] == "processing"
    caption_job = db.get(Job, replacement.caption_review["job_id"])
    assert caption_job.payload["revision"] == 2
    assert caption_job.payload["source_caption"] == "original caption context"


def test_playlist_match_requires_one_exact_unused_catalog_track():
    def track(id, title, artist):
        return {
            "track": {
                "audio_asset_id": id,
                "audio_cluster_id": f"3{id}",
                "title": title,
                "display_artist": artist,
            }
        }

    catalog = {
        "Ambiguous Artist": [track("1", "Ambiguous", "Artist"), track("2", "Ambiguous", "Artist")],
        "Used Artist": [track("9", "Used", "Artist")],
        "Valid - slowed Artist": [
            track("5", "Valid (slowed)", "Artist"),
            track("7", "Valid (demo)", "Artist"),
            track("8", "Valid", "Artist"),
        ],
    }

    def search(query, product):
        assert product == "music_in_feed"
        return {"items": catalog[query]}

    adapter = SimpleNamespace(client=SimpleNamespace(music_search_v2=search))
    checks = []
    result = match_track(
        adapter,
        [
            ("a", "Ambiguous", "Artist"),
            ("b", "Used", "Artist"),
            ("c", "Valid - slowed (feat. Guest)", "Artist, Guest"),
        ],
        set(),
        used_clusters={"39"},
        checks=checks,
    )
    assert result["spotify_id"] == "c" and result["catalog_id"] is None
    assert result["feed_music"] == {
        "audio_asset_id": "5",
        "audio_cluster_id": "35",
        "title": "Valid (slowed)",
        "artist": "Artist",
        "start_ms": 0,
    }
    assert [check["state"] for check in checks] == ["ambiguous", "already_used", "matched"]


def test_named_song_uses_artist_context_and_resolves_clean_album_pair():
    caption = "My favorite song on the album. Coffee Bean talks about Travis’ relationship."
    assert named_song(caption) == ("Coffee Bean", "Coffee Bean Travis")
    explicit = {
        "title": "COFFEE BEAN",
        "display_artist": "Travis Scott",
        "audio_asset_id": "51",
        "audio_cluster_id": "61",
        "duration_in_ms": 209115,
        "is_explicit": True,
    }
    clean = {**explicit, "audio_asset_id": "52", "audio_cluster_id": "62", "is_explicit": False}
    other = {
        **explicit,
        "audio_asset_id": "53",
        "audio_cluster_id": "63",
        "display_artist": "Another Artist",
    }
    catalog = [clean, other, explicit]
    adapter = SimpleNamespace(
        client=SimpleNamespace(
            music_search_v2=lambda query, product: {"items": [{"track": item} for item in catalog]},
        )
    )
    track, status = named_track(adapter, "Coffee Bean", "Coffee Bean Travis", caption)
    assert status == "named_matched" and track["audio_asset_id"] == "51"
    track, _ = named_track(
        adapter, "Coffee Bean", "Coffee Bean Travis", caption + " Clean version."
    )
    assert track["audio_asset_id"] == "52"
    clean["duration_in_ms"] -= 10000
    assert named_track(adapter, "Coffee Bean", "Coffee Bean Travis", caption) == (None, "ambiguous")
    assert named_track(adapter, "Coffee Bean", "Coffee Bean", "This song has a nice beat.") == (
        None,
        "no_match",
    )


def test_source_audio_wins_before_named_song_and_playlist():
    music_id, track, selection, checks = dm_intake.music_for_share(
        None,
        None,
        None,
        None,
        {
            "id": "7",
            "title": "Attached song",
            "artist": "Artist",
            "audio_asset_id": "31",
            "audio_cluster_id": "32",
            "start_ms": 3500,
        },
        "Coffee Bean is an album closer by Travis Scott.",
        "",
        True,
        "playlist",
    )
    assert music_id is None and track["audio_asset_id"] == "31" and track["start_ms"] == 3500
    assert selection is None and checks[0]["state"] == "source_matched"


def test_source_audio_is_resolved_from_title_and_artist_when_ids_are_missing():
    guard = SimpleNamespace(reserve_dm_requests=lambda units: units)

    def search(query, product):
        assert product == "music_in_feed" and query == "Attached song Artist"
        return {
            "items": [
                {
                    "track": {
                        "title": "Attached song",
                        "display_artist": "Artist",
                        "audio_asset_id": "31",
                        "audio_cluster_id": "32",
                    }
                }
            ]
        }

    adapter = SimpleNamespace(client=SimpleNamespace(music_search_v2=search))
    _, track, selection, checks = dm_intake.music_for_share(
        None,
        None,
        adapter,
        guard,
        {"id": None, "title": "Attached song", "artist": "Artist"},
        "",
        "",
        True,
        None,
    )
    assert guard.music_queries == {"Attached song Artist"}
    assert selection is None and track["audio_asset_id"] == "31"
    assert checks[0]["state"] == "source_matched"


def test_playlist_context_rank_preserves_no_repeat_selection(system):
    _, db, account, _ = system
    draft = ContentDraft(account_id=account.id, asset_id="asset")
    db.add(draft)
    db.flush()
    db.add_all(
        [
            PlaylistTrack(
                playlist_id="test", spotify_id="a", title="Midnight haze", artist="Artist A"
            ),
            PlaylistTrack(
                playlist_id="test", spotify_id="b", title="Summer party", artist="Artist B"
            ),
            PlaylistTrack(playlist_id="test", spotify_id="c", title="Night fog", artist="Artist C"),
            PlaylistSelection(
                account_id=account.id,
                draft_id=draft.id,
                spotify_id="c",
                song_key=song_key("Night fog", "Artist C"),
                title="Night fog",
                artist="Artist C",
            ),
        ]
    )
    db.flush()
    assert [
        item[0]
        for item in playlist_candidates(db, account.id, "test", "A quiet night in dense fog.")
    ] == ["a", "b"]


def test_caption_reports_model_timeout_and_source_caption_fallback(monkeypatch):
    results = iter([(None, "timeout"), ("A blue bicycle rests by the wall.", None)])
    monkeypatch.setattr(caption_writer, "chat", lambda *args, **kwargs: next(results))
    events = []
    rendered = caption_writer.write_caption(
        "writer",
        "vision",
        "image",
        "A blue bicycle is parked outdoors.",
        [],
        report=lambda name, detail: events.append((name, detail)),
    )
    assert rendered == "a blue bicycle rests by the wall."
    assert [(stage, item["state"]) for stage, item in events] == [
        ("vision", "running"),
        ("vision", "failed"),
        ("writing", "running"),
        ("writing", "complete"),
        ("result", "complete"),
    ]
    assert events[1][1]["reason"] == "timeout" and events[3][1]["model"] == "writer"


@pytest.mark.parametrize(
    ("writer", "reason"),
    [
        ((None, "timeout"), "timeout"),
        (
            ("The woman on the right has a bracelet on her right wrist.", None),
            "literal_photo_description",
        ),
        (
            ("The bracelet sits on her right wrist near the cuff.", None),
            "literal_photo_description",
        ),
    ],
)
def test_caption_rejects_raw_or_rephrased_vision_notes(monkeypatch, writer, reason):
    results = iter(
        [("The woman on the right has a bracelet on her right wrist.", None), writer, writer]
    )
    monkeypatch.setattr(caption_writer, "chat", lambda *args, **kwargs: next(results))
    events = {}
    assert (
        caption_writer.write_caption(
            "writer",
            "vision",
            "image",
            "",
            [],
            report=lambda name, detail: events.update({name: detail}),
        )
        is None
    )
    assert events["result"] == {"state": "failed", "reason": reason}


def test_caption_rewrites_printed_meme_text_without_replaying_it(monkeypatch):
    printed = "She says that a friend could be an absent father."
    results = iter(
        [
            ('A portrait with text that reads "A friend could be an absent father".', None),
            (printed, None),
            ("a deadpan self roast for the friend who rarely shows up.", None),
        ]
    )
    monkeypatch.setattr(caption_writer, "chat", lambda *args, **kwargs: next(results))
    events = {}
    assert (
        caption_writer.write_caption(
            "writer",
            "vision",
            "image",
            "",
            [],
            report=lambda name, detail: events.update({name: detail}),
        )
        == "a deadpan self roast for the friend who rarely shows up."
    )
    assert events["revision"]["state"] == "complete"


def test_archive_caption_uses_supported_detail_when_writer_times_out(monkeypatch):
    calls = []

    def unavailable(*args, **kwargs):
        calls.append(args[0])
        return None, "timeout"

    monkeypatch.setattr(caption_writer, "chat", unavailable)
    source = "The track “Coffee Bean” closes the album."
    assert caption_writer.check_caption(
        "the album “coffee bean” closes with a quieter sound.", source, None, []
    ) == (None, "unsupported_subject")
    assert caption_writer.write_caption("writer", None, None, source, []) is None
    assert calls == ["writer"]
    assert (
        caption_writer.archive_caption(source, "1")
        == "coffee bean gets the last word on the record."
    )
    assert caption_writer.check_caption(
        'the track "close" supplied the final beat.', source, None, []
    ) == (None, "unsupported_quote")


def test_meme_text_uses_meme_prompt_without_unrelated_reference_material(monkeypatch):
    def chat(model, messages, *args, **kwargs):
        if model == "vision":
            return 'A person is posing with text that reads "I rarely show up for friends".', None
        assert "printed_meme_text" in messages[1]["content"]
        assert "bicycle" not in messages[1]["content"] and "posing" not in messages[1]["content"]
        return "a friendship with very generous out of office hours.", None

    monkeypatch.setattr(caption_writer, "chat", chat)
    assert (
        caption_writer.write_caption(
            "writer",
            "vision",
            "image",
            "",
            ["A bicycle shop with a long history."],
        )
        == "a friendship with very generous out of office hours."
    )


def test_caption_checks_for_printed_text_when_first_pass_reports_speech(monkeypatch):
    monkeypatch.setattr(caption_writer.shutil, "which", lambda name: None)
    results = iter(
        [
            ("A young woman asks if a friend could be unreliable.", None),
            ("A friendship that only works part time.", None),
            ("a friend with very generous out of office hours.", None),
        ]
    )
    monkeypatch.setattr(caption_writer, "chat", lambda *args, **kwargs: next(results))
    events = {}
    assert (
        caption_writer.write_caption(
            "writer",
            "vision",
            "image",
            "",
            [],
            report=lambda name, detail: events.update({name: detail}),
        )
        == "a friend with very generous out of office hours."
    )
    assert events["text_scan"]["state"] == "complete"


def test_caption_uses_dedicated_text_reader_before_second_vision_call(monkeypatch):
    monkeypatch.setattr(caption_writer.shutil, "which", lambda name: "/usr/bin/tesseract")
    monkeypatch.setattr(
        caption_writer,
        "read_printed_text",
        lambda path, image: "a friendship that only works part time",
    )

    def chat(model, messages, *args, **kwargs):
        if model == "vision":
            assert messages[0]["content"] == caption_writer.VISION_PROMPT
            return "A young woman is saying something about a friend.", None
        assert "printed_meme_text" in messages[1]["content"]
        return "a friend with very generous out of office hours.", None

    monkeypatch.setattr(caption_writer, "chat", chat)
    events = {}
    assert (
        caption_writer.write_caption(
            "writer",
            "vision",
            "image",
            "",
            [],
            report=lambda stage, detail: events.update({stage: detail}),
        )
        == "a friend with very generous out of office hours."
    )
    assert events["ocr"]["model"] == "Tesseract"


def test_caption_holds_if_reported_speech_cannot_be_verified_as_printed_text(monkeypatch):
    monkeypatch.setattr(caption_writer.shutil, "which", lambda name: None)
    results = iter(
        [
            ("A young woman is saying a friend could be an absent father.", None),
            ("A young woman asks if a friend could be an absent father.", None),
        ]
    )
    monkeypatch.setattr(caption_writer, "chat", lambda *args, **kwargs: next(results))
    events = {}
    assert (
        caption_writer.write_caption(
            "writer",
            "vision",
            "image",
            "",
            [],
            report=lambda stage, detail: events.update({stage: detail}),
        )
        is None
    )
    assert events["result"] == {"state": "failed", "reason": "no_context"}
    assert "writing" not in events


def test_self_audio_share_is_deduped_and_never_queues_a_photo_draft(system):
    _, db, account, _ = system
    account.instagram_id = "42"
    inbox = DmInbox(account_id=account.id, sender_id="42", sender_username=account.username)
    db.add(inbox)
    db.flush()
    at = now() + timedelta(seconds=1)
    payload = {
        "item_type": "reels_audio_share",
        "reels_audio_share": [
            {
                "target_url": "https://www.instagram.com/_n/reels_audio_page?audio_id=143",
                "header_title_text": "Audio title",
                "header_subtitle_text": "Artist·1K reels",
            }
        ],
    }
    parsed = dm_reader.audio_shares(payload)
    assert parsed[0].id == "143" and parsed[0].artist == "Artist"
    batch = [DmMessage(i, "42", at, None, None, audio=parsed) for i in ("12", "11")]
    dm_intake.record_page(db, inbox, "10", None, batch, at)
    row = db.get(SavedAudio, (account.id, "143"))
    assert row.title == "Audio title"
    assert [job.kind for job in db.scalars(select(Job))] == ["dm_audio"]
    assert db.scalar(select(DmSubmission.id)) is None
    row.state = "feed_ready"
    row.feed_music = {"audio_asset_id": "123", "audio_cluster_id": "124"}
    db.flush()
    assert saved_audio.audio_candidate(db, account.id)[0]["audio_asset_id"] == "123"
    saved_audio.mark_used(db, account.id, row.feed_music, at)
    assert saved_audio.audio_candidate(db, account.id) is None


def test_self_shares_queue_processing_and_final_receipts_only_once(system, monkeypatch):
    _, db, account, _ = system
    account.instagram_id = "42"
    config = settings().model_copy(
        update={"dm_self_receipts": True, "dm_default_account_id": account.id}
    )
    monkeypatch.setattr(dm_receipts, "settings", lambda: config)
    at = now()
    inbox = DmInbox(
        account_id=account.id,
        sender_id="42",
        sender_username=account.username,
        thread_id="10",
        direct_publish_from=at,
        created_at=at,
    )
    db.add(inbox)
    db.flush()
    reel_url = "https://www.instagram.com/reel/Clip_1/"
    audio = dm_reader.SharedAudio("143", "Title", "Artist")
    messages = [
        DmMessage("12", "42", at, reel_url, None),
        DmMessage("11", "42", at, None, None, audio=(audio,)),
    ]
    dm_intake.record_page(db, inbox, "10", None, messages, at)
    dm_intake.record_page(db, inbox, "10", None, messages, at)
    jobs = {job.kind: job for job in db.scalars(select(Job).where(Job.kind != "dm_receipt"))}
    jobs["dm_import"].payload = {**jobs["dm_import"].payload, "reel_url": reel_url}
    db.get(SavedAudio, (account.id, "143")).state = "feed_unmatched"
    for job in jobs.values():
        dm_receipts.queue_receipt(db, account, job, "succeeded")
        dm_receipts.queue_receipt(db, account, job, "succeeded")
    receipts = db.scalars(select(Job).where(Job.kind == "dm_receipt")).all()
    assert len(receipts) == 4
    assert all(job.priority > jobs["dm_import"].priority for job in receipts)
    assert all(job.payload["thread_id"] == "10" for job in receipts)
    assert {job.payload["text"].splitlines()[0] for job in receipts} == {
        "Archive Desk: Received. Processing your share.",
        "Archive Desk: Received. Checking this sound.",
        "Archive Desk: Reel source link saved.",
        "Archive Desk: Sound saved. Instagram’s feed music catalog had no verified match.",
    }
    duplicate = DmMessage("13", "42", at, None, None, audio=(audio,))
    dm_intake.record_page(db, inbox, "10", None, [duplicate, *messages], at)
    dm_intake.record_page(db, inbox, "10", None, [duplicate, *messages], at)
    receipts = db.scalars(select(Job).where(Job.kind == "dm_receipt")).all()
    assert len(receipts) == 5
    assert any("already saved" in job.payload["text"] for job in receipts)


def test_spotify_sync_is_durable_and_preserves_prior_snapshot_on_error(system, monkeypatch):
    _, db, account, _ = system
    account_id = account.id
    db.commit()
    expected = {"spotify_id": "id", "title": "Track", "artist": "Artist"}
    calls = []

    def fetch(playlist_id):
        calls.append(playlist_id)
        if len(calls) > 1:
            raise HTTPException(502, "Unavailable")
        return [expected]

    monkeypatch.setattr(spotify_playlist, "public_tracks", fetch)
    at = now()
    interval = timedelta(hours=4)
    args = (db.get_bind(), account_id, "playlist", interval)
    assert spotify_playlist.sync_playlist(*args, clock=lambda: at)
    assert not spotify_playlist.sync_playlist(*args, clock=lambda: at + timedelta(hours=1))
    assert spotify_playlist.sync_playlist(*args, clock=lambda: at + interval)
    assert len(calls) == 2
    assert db.get(PlaylistTrack, ("playlist", "id")).title == "Track"


def test_caption_reference_order_prefers_multiple_examples_from_primary_profile(system):
    _, db, account, _ = system
    primary = [
        "A new silhouette emerged from the demands of downtown cycling.",
        "The compact streets helped make small cars a familiar sight.",
    ]
    secondary = "Early skate videos helped bring this label to a wider audience."
    for target, captions in (("other", [secondary]), ("hidden.ny", primary)):
        job = Job(account_id=account.id, kind="insight", state="succeeded", idempotency_key=target)
        db.add(job)
        db.flush()
        db.add(
            InsightCheck(
                account_id=account.id,
                job_id=job.id,
                kind="posts",
                target=target,
                items=[{"caption": caption} for caption in captions],
                observed_at=now(),
            )
        )
    db.flush()
    assert caption_writer.style_examples(db, account.id, "hidden.ny,other") == [*primary, secondary]


def test_caption_uses_substantive_source_without_vision(monkeypatch):
    source = (
        "The designer developed this collection over several years while studying old cycling "
        "uniforms and their influence on the way people dress for work in the city."
    )
    events = {}

    def chat(name, messages, *args, **kwargs):
        assert name == "writer" and source in messages[1]["content"]
        return "old cycling uniforms shaped the collection , down to its workwear details.", None

    monkeypatch.setattr(caption_writer, "chat", chat)
    assert (
        caption_writer.write_caption(
            "writer",
            "vision",
            "image",
            source,
            [],
            report=lambda stage, detail: events.update({stage: detail}),
        )
        == "old cycling uniforms shaped the collection , down to its workwear details."
    )
    assert events["vision"] == {"state": "skipped", "reason": "source_context_used"}


def test_playlist_selections_survive_playlist_refresh_and_exclude_duplicate_recordings(system):
    _, db, account, _ = system
    draft = ContentDraft(account_id=account.id, asset_id="asset")
    db.add(draft)
    db.flush()
    db.add(
        PlaylistSelection(
            account_id=account.id,
            draft_id=draft.id,
            spotify_id="id1",
            catalog_id="9",
            title="Picked Song",
            artist="Singer",
            song_key=song_key("Picked Song", "Singer"),
        )
    )
    db.add_all(
        PlaylistTrack(playlist_id="pool", spotify_id=id, title=title, artist="Singer")
        for id, title in (("id1", "Revised label"), ("id2", "Picked Song"), ("id3", "New Song"))
    )
    db.flush()
    assert [track.spotify_id for track in available_tracks(db, account.id, "pool")] == ["id3"]


def test_carousel_fetch_preserves_photo_slots_and_reserves_extra_requests(monkeypatch):
    resources = [
        SimpleNamespace(media_type=1, thumbnail_url="first"),
        SimpleNamespace(media_type=2, thumbnail_url="ignored"),
        SimpleNamespace(media_type=1, thumbnail_url="second"),
    ]
    media = SimpleNamespace(media_type=8, resources=resources, caption_text="Shared album")
    client = SimpleNamespace(media_info_v1=lambda _: media)
    requested = []
    guard = SimpleNamespace(reserve_carousel_downloads=lambda count: requested.append(count))
    monkeypatch.setattr(dm_reader, "download_photo", lambda url, _: url.encode())
    photos, caption, skipped, track = dm_reader.read_shared_photos(client, "321", guard)
    assert track is None
    assert requested == [1]
    assert list(photos) == [b"first", b"second"]
    assert caption == "Shared album" and skipped == 1


def test_dm_reorders_carousel_by_stable_numbers(system):
    _, db, account, _ = system
    at = now()
    draft = ContentDraft(
        account_id=account.id,
        asset_id="one",
        kind="feed_carousel",
        photo_assets=["one", "two", "three"],
        photo_order=[1, 2, 3],
        status="draft",
        revision=1,
    )
    submission = DmSubmission(account_id=account.id, sender_id="555", thread_id="7", message_id="6")
    inbox = DmInbox(account_id=account.id, sender_id="555", sender_username="owner")

    for text, expected, revision, status in (
        ("^ order: 3, 1, 2", [3, 1, 2], 2, "edits_saved"),
        ("^ order: 2 3 1", [2, 3, 1], 3, "edits_saved"),
        ("^ order: 1 1 2", [2, 3, 1], 3, "review_commands"),
    ):
        key, value = read_command({"item_type": "text", "text": text})
        submission.draft_commands = {
            "commands": {key: {"value": value, "id": "7", "at": at.isoformat()}}
        }
        dm_intake.apply_draft_commands(db, inbox, submission, draft)
        assert draft.photo_order == expected and draft.revision == revision
        assert submission.draft_commands["status"] == status


def test_original_music_is_taken_from_the_shared_media_and_blocks_silent_autopost(system):
    _, db, account, _ = system
    client = SimpleNamespace(
        last_json={
            "items": [
                {
                    "pk": "321",
                    "music_metadata": {
                        "music_info": {
                            "music_asset_info": {
                                "music_canonical_id": "123456",
                                "title": "Fixture song",
                                "display_artist": "Fixture artist",
                            }
                        }
                    },
                }
            ]
        }
    )
    assert dm_reader.observed_track(client, "999") is None
    track = dm_reader.observed_track(client, "321_100")
    assert track == {"id": "123456", "title": "Fixture song", "artist": "Fixture artist"}
    draft = ContentDraft(
        account_id=account.id, asset_id="first", kind="feed_carousel", status="draft", revision=1
    )
    submission = DmSubmission(account_id=account.id, sender_id="555", thread_id="7", message_id="6")
    inbox = DmInbox(account_id=account.id, sender_id="555", sender_username="owner")
    key, value = read_command({"item_type": "text", "text": "^ music: Original"})
    commands = {key: {"id": "8", "value": value}, "publish": {"id": "9", "value": "autopost"}}
    submission.draft_commands = {"commands": commands}
    dm_intake.apply_draft_commands(db, inbox, submission, draft)
    assert submission.draft_commands["status"] == "source_music_unavailable"
    assert draft.revision == 1 and db.scalar(select(Job)) is None

    draft.source_music = track
    submission.draft_commands = {"commands": {key: commands[key]}}
    dm_intake.apply_draft_commands(db, inbox, submission, draft)
    assert draft.music_canonical_id == "123456" and draft.revision == 2


def test_randomize_is_saved_once_and_draft_shorthand_holds_publication(system):
    _, db, account, _ = system
    draft = ContentDraft(
        account_id=account.id,
        asset_id="a",
        kind="feed_carousel",
        status="draft",
        revision=1,
        photo_assets=["a", "b", "c"],
        photo_order=[1, 2, 3],
    )
    submission = DmSubmission(account_id=account.id, sender_id="555", thread_id="7", message_id="6")
    inbox = DmInbox(account_id=account.id, sender_id="555", sender_username="owner")
    commands = {}
    for identifier, text in enumerate(("^ order: randomize", "^ fit: original", "^ draft"), 7):
        key, value = read_command({"item_type": "text", "text": text})
        commands[key] = {"id": str(identifier), "value": value, "at": now().isoformat()}
    submission.draft_commands = {"commands": commands}
    dm_intake.apply_draft_commands(db, inbox, submission, draft)
    shuffled = list(draft.photo_order)
    assert sorted(shuffled) == [1, 2, 3] and shuffled != [1, 2, 3]
    assert draft.fit_mode == "native" and submission.draft_commands["status"] == "held_as_draft"
    dm_intake.apply_draft_commands(db, inbox, submission, draft)
    assert draft.revision == 2 and draft.photo_order == shuffled
    assert read_command({"item_type": "text", "text": "^ autopost"}) == ("publish", "autopost")
    assert db.scalar(select(Job)) is None


def test_fresh_share_waits_for_next_inbox_check_and_accepts_explicit_overrides(system, monkeypatch):
    _, db, account, _ = system
    at = now()
    config = settings().model_copy(
        update={
            "dm_default_account_id": account.id,
            "dm_default_from": at - timedelta(seconds=1),
            "dm_poll_interval_seconds": 60,
        }
    )
    monkeypatch.setattr(dm_intake, "settings", lambda: config)
    inbox = DmInbox(
        account_id=account.id,
        sender_id="555",
        sender_username="owner",
        created_at=at - timedelta(minutes=10),
        direct_publish_from=at - timedelta(minutes=5),
        version=1,
    )
    db.add(inbox)
    share = DmMessage("10", "555", at, "321", None)
    archived = DmMessage("9", "555", at - timedelta(seconds=20), "654", None)
    dm_intake.record_page(db, inbox, "7", None, [share, archived], at)
    imports = {
        item.message_id: db.get(Job, item.job_id) for item in db.scalars(select(DmSubmission))
    }
    assert "default_share_at" not in imports["9"].payload
    assert imports["10"].priority == -1
    assert aware(imports["10"].scheduled_at) == at + timedelta(seconds=75)
    later = at + timedelta(seconds=60)
    dm_intake.record_page(
        db,
        inbox,
        "7",
        None,
        [
            DmMessage("12", "555", later, None, None, ("publish", "draft")),
            DmMessage("11", "555", later, None, None, ("order", "original")),
            share,
        ],
        later,
    )
    submission = db.scalar(select(DmSubmission).where(DmSubmission.message_id == "10"))
    draft = ContentDraft(
        account_id=account.id,
        asset_id="a",
        kind="feed_carousel",
        status="draft",
        photo_assets=["a", "b", "c"],
        photo_order=[1, 2, 3],
        revision=1,
    )
    dm_intake.apply_share_defaults(inbox, submission, draft, imports["10"].payload)
    dm_intake.apply_draft_commands(db, inbox, submission, draft)
    assert draft.photo_order == [1, 2, 3]
    assert submission.draft_commands["status"] == "held_as_draft"


def test_default_feed_publish_needs_usable_caption_and_followup_poll(system, monkeypatch):
    _, db, account, _ = system
    at = now()
    calls = []
    monkeypatch.setattr(
        content_publication, "queue_publication", lambda *args, **kwargs: calls.append(args[2])
    )
    inbox = DmInbox(account_id=account.id, sender_id="555", direct_publish_from=at, version=1)
    payload = {
        "default_share_at": (at + timedelta(seconds=1)).isoformat(),
        "default_check_after": (at + timedelta(seconds=45)).isoformat(),
    }
    for polled, expected in (
        (None, "default_held"),
        (at + timedelta(seconds=60), "publication_queued"),
        (at + timedelta(seconds=60), "review_publication"),
    ):
        inbox.last_polled_at = polled
        submission = DmSubmission(
            account_id=account.id, sender_id="555", thread_id="7", message_id="10"
        )
        draft = ContentDraft(
            account_id=account.id,
            asset_id="a",
            kind="feed_carousel",
            status="draft",
            photo_assets=["a", "b", "c"],
            photo_order=[1, 2, 3],
            revision=1,
        )
        submission.caption_command = {
            "state": "generation_failed" if expected == "review_publication" else "generated"
        }
        dm_intake.apply_share_defaults(inbox, submission, draft, payload)
        dm_intake.apply_draft_commands(db, inbox, submission, draft)
        assert sorted(draft.photo_order) == [1, 2, 3] and draft.photo_order != [1, 2, 3]
        assert submission.draft_commands["status"] == expected
    assert len(calls) == 1


def test_generated_caption_normalizes_punctuation_and_rejects_source_copy():
    text = "Hand-painted signs frame the storefront, with gold lettering against dark blue."
    assert normalize_caption(text, []) == (
        "hand-painted signs frame the storefront , with gold lettering against dark blue."
    )
    assert normalize_caption(text, [text]) is None
    assert normalize_caption("A painting with a caption by @somebody.", []) is None


@pytest.mark.parametrize("publish_command", ["^ publish: story", "^ autopost"])
def test_fresh_direct_command_queues_exact_story_and_revocation_holds_it(
    system, monkeypatch, publish_command
):
    _, db, account, _ = system
    monkeypatch.setattr(
        content_publication, "settings", lambda: SimpleNamespace(adapter_mode="live")
    )
    at = now()
    account.mode, account.session_state, account.read_only_verified_at = "live", "connected", at
    image = ContentAsset(
        account_id=account.id, sha256="a" * 64, mime="image/png", byte_size=1, width=32, height=32
    )
    inbox = DmInbox(
        account_id=account.id,
        sender_id="555",
        sender_username="owner",
        direct_publish_from=at - timedelta(minutes=1),
    )
    db.add_all([image, inbox])
    db.flush()
    draft = ContentDraft(account_id=account.id, asset_id=image.id)
    db.add(draft)
    db.flush()
    submission = DmSubmission(
        account_id=account.id,
        sender_id=inbox.sender_id,
        thread_id="7",
        message_id="1",
        draft_id=draft.id,
        media_ref="700",
    )
    db.add(submission)
    db.flush()

    commands = {}
    for index, text in enumerate(["^ story: Today", publish_command], start=2):
        key, value = read_command({"item_type": "text", "text": text})
        commands[key] = {"id": str(index), "value": value, "at": at.isoformat()}
    stale = {
        **commands,
        "publish": {**commands["publish"], "at": (at - timedelta(hours=1)).isoformat()},
    }
    submission.draft_commands = {"commands": stale}
    dm_intake.apply_draft_commands(db, inbox, submission, draft)
    assert submission.draft_commands["status"] == "review_publication"
    assert db.scalar(select(Job)) is None

    submission.draft_commands = {"commands": commands}
    dm_intake.apply_draft_commands(db, inbox, submission, draft)
    assert submission.draft_commands["status"] == "publication_queued"
    job = db.scalar(select(Job))
    assert job.kind == "publish_story"
    assert job.payload["snapshot"]["overlay_text"] == "Today"
    assert content_publication.validate_intent(db, job).id == draft.id
    inbox.direct_publish_from = None
    with pytest.raises(OperationBlocked, match="permission changed"):
        content_publication.validate_intent(db, job)


def test_caption_targets_the_preceding_approved_share_across_pages(system):
    _, db, account, _ = system
    at = now()
    inbox = DmInbox(
        account_id=account.id,
        sender_id="555",
        sender_username="owner",
        created_at=at - timedelta(minutes=1),
        watermark="10",
    )
    stored = DmSubmission(
        account_id=account.id, sender_id="555", thread_id="123", message_id="9", media_ref="700"
    )
    db.add_all([inbox, stored])
    db.commit()

    text = {"item_type": "text", "text": "^ caption: chip em"}
    caption = caption_instruction(text)
    assert caption == "chip em"
    dm_intake.record_page(
        db,
        inbox,
        "123",
        "older",
        [
            DmMessage("12", "555", at, None, caption),
            DmMessage("11", "555", at, None, "Old instruction"),
            DmMessage("10", "999", at, None, None),
        ],
        at,
    )
    db.commit()
    db.expire_all()
    assert inbox.pending_caption["reached_watermark"]
    dm_intake.record_page(
        db,
        inbox,
        "123",
        "even-older",
        [
            DmMessage("9", "555", at, "700", None),
            DmMessage("8", "555", at, "701", None),
        ],
        at,
    )
    assert stored.caption_command == {"id": "12", "value": "chip em", "state": "pending"}
    assert inbox.watermark == "12" and not inbox.cursor and not inbox.pending_caption
    assert len(db.scalars(select(DmSubmission)).all()) == 1
    assert db.scalar(select(Job)) is None


@pytest.mark.parametrize("remove_cap", [False, True])
def test_automatic_dm_poll_requires_direct_grant_and_runs_once_per_interval(
    system, monkeypatch, remove_cap
):
    _, db, account, _ = system
    account_id = account.id
    at = now()
    monkeypatch.setattr(dm_intake, "settings", lambda: SimpleNamespace(dm_poll_interval_seconds=60))
    account.mode, account.session_state = "live", "connected"
    inbox = DmInbox(account_id=account_id, sender_id="555", sender_username="owner")
    db.add(inbox)
    values = SettingsValues(request_ceiling=2).model_dump()
    db.add(AccountSettings(account_id=account_id, values=values))
    db.commit()
    engine = db.get_bind()
    dm_intake.materialize_direct_polls(engine, lambda: at)
    assert db.scalar(select(Job)) is None
    inbox.direct_publish_from = at
    db.commit()
    dm_intake.materialize_direct_polls(engine, now)
    db.expire_all()
    first = db.scalar(select(Job))
    assert first and first.kind == "dm_poll"
    assert first.payload["automatic"] is True
    created = first.created_at.replace(tzinfo=at.tzinfo)
    first.state = "succeeded"
    first_id = first.id
    db.commit()
    dm_intake.materialize_direct_polls(engine, lambda: created + timedelta(seconds=59))
    assert [job.id for job in db.scalars(select(Job))] == [first_id]
    db.commit()
    dm_intake.materialize_direct_polls(engine, lambda: created + timedelta(seconds=60))
    jobs = db.scalars(select(Job)).all()
    assert len(jobs) == 2
    latest = next(job for job in jobs if job.id != first_id)
    latest.state, latest.error = "blocked", "BUDGET_EXHAUSTED"
    db.add(
        BudgetUsage(
            account_id=account_id,
            local_day=local_day(created, values),
            category="requests",
            consumed=2,
        )
    )
    db.commit()
    dm_intake.materialize_direct_polls(engine, lambda: created + timedelta(minutes=5))
    assert len(db.scalars(select(Job)).all()) == 2
    db.commit()
    if remove_cap:
        db.get(AccountSettings, account_id).values = {**values, "request_ceiling": None}
        db.commit()
    at = created + (timedelta(minutes=5) if remove_cap else timedelta(days=1))
    dm_intake.materialize_direct_polls(engine, lambda: at)
    assert len(db.scalars(select(Job)).all()) == 3


@pytest.mark.skipif(not os.getenv("TEST_POSTGRES_URL"), reason="Requires PostgreSQL account lane")
@pytest.mark.parametrize("writer_available", [True, False])
def test_dm_cursor_dedupe_and_reviewed_feed_and_story(
    postgres, monkeypatch, tmp_path, request, writer_available
):
    engine, aid = postgres
    config = settings().model_copy(
        update={"adapter_mode": "live", "content_store_path": str(tmp_path)}
    )
    monkeypatch.setattr(content_drafts, "settings", lambda: config)
    monkeypatch.setattr(dm_intake, "settings", lambda: config)
    monkeypatch.setattr(content_publication, "settings", lambda: config)
    data = io.BytesIO()
    Image.new("RGB", (160, 100), "green").save(data, "PNG")
    original = data.getvalue()
    storage = upload_backend(config)
    if storage in REMOTE_BACKENDS:
        # Only the generated PostgreSQL fixture account and this image hash are in scope.
        key = SimpleNamespace(
            account_id=aid, sha256=validate_image(original)["sha256"], storage_backend=storage
        )
        bucket, args = bucket_args(config, key)
        request.addfinalizer(lambda: bucket.delete_object(**args))
    at = now()
    with Session(engine) as db:
        account = db.get(Account, aid)
        account.instagram_id = "999"
        account.mode = "live"
        account.session_state = "connected"
        account.read_only_verified_at = at
        account.paused = False
        sender = Account(
            username="sender",
            mode="live",
            instagram_id="555",
            session_state="connected",
            read_only_verified_at=at,
        )
        db.add(sender)
        db.add(
            AccountSettings(account_id=aid, values=SettingsValues(request_ceiling=50).model_dump())
        )
        db.commit()
        dm_intake.configure_inbox(
            aid, dm_intake.InboxSettings(sender_account_id=sender.id, enabled=True), db
        )
        db.get(DmInbox, (aid, "555")).created_at = at - timedelta(minutes=1)
        db.commit()
        poll_id = dm_intake.poll_inbox(aid, db).id
        assert dm_intake.poll_inbox(aid, db).id == poll_id

    def message(mid, sender, media):
        return {
            "item_type": "media_share",
            "media_share": {"pk": media},
            "item_id": mid,
            "user_id": sender,
            "timestamp": str(int(at.timestamp() * 1_000_000)),
        }

    control = SimpleNamespace(
        pages=[], imports=[], writes=[], kind=None, caption=None, music=None, mid=None
    )

    def photo(client, reference, guard):
        guard.before_request()
        guard.before_request()
        control.imports.append(reference)
        return [original], "Incoming photo", 0, None

    monkeypatch.setattr(dm_intake, "read_shared_photos", photo)

    class Adapter:
        def __init__(self, guard):
            self.guard = guard
            self.client = self

        def direct_thread_by_participants(self, users):
            assert users == ["555"]
            self.guard.before_request()
            return {"thread_v2_id": "123"}

        def private_request(self, path, params):
            assert path == "direct_v2/threads/123/"
            self.guard.before_request()
            cursor = params.get("cursor")
            control.pages.append(cursor)
            command = {
                **message("4", "555", "700"),
                "item_type": "text",
                "text": "^ caption: chip em",
            }
            items = [command, message("3", "555", "700"), message("2", "999", "701")]
            if cursor:
                items = [message("1", "555", "702")]
            has_more = len(control.pages) == 1
            return {
                "thread": {
                    "thread_v2_id": "123",
                    "users": [{"pk": "555"}],
                    "items": items,
                    "has_older": has_more,
                    "oldest_cursor": "older" if has_more else None,
                }
            }

        def publish_photo(self, path, upload_id, values):
            kind = values["kind"]
            with Image.open(path) as image:
                assert image.size == ((1080, 1920) if kind == "story_photo" else (320, 200))
            self.guard.before_request(media={"operation": "upload", "upload_id": upload_id})
            operation = "configure_story" if kind == "story_photo" else "configure_photo"
            self.guard.before_request(
                media={
                    "operation": operation,
                    "upload_id": upload_id,
                    "caption": values["caption"],
                    "music_canonical_id": values.get("music_canonical_id"),
                    "playlist_music": values.get("playlist_music"),
                }
            )
            control.writes.append(operation)
            control.kind, control.caption = kind, values["caption"]
            control.music = values.get("playlist_music")
            control.mid = f"{990 + len(control.writes)}_999"
            return {"media_id": control.mid}

        def owned_media(self, media_id, actor):
            self.guard.before_request()
            assert media_id == control.mid and actor == "999"
            return {
                "media_id": control.mid,
                "caption": control.caption,
                "kind": control.kind,
                "playlist_music": control.music,
            }

        def music_search_v2(self, query, product):
            assert query == "Fixture Song Fixture Artist" and product == "music_in_feed"
            self.guard.before_request()
            return {
                "items": [
                    {
                        "track": {
                            "title": "Fixture Song",
                            "display_artist": "Fixture Artist",
                            "audio_asset_id": "5555",
                            "audio_cluster_id": "5556",
                        }
                    }
                ]
            }

        def edit_caption(self, media_id, caption, observation):
            assert observation["media_id"] == media_id == control.mid
            self.guard.before_request(
                media={"operation": "edit_caption", "media_id": media_id, "caption": caption}
            )
            control.writes.append("edit_caption")
            control.caption = caption

    executor = Executor(engine, Adapter)
    assert executor.run_account(aid) and executor.run_account(aid)
    with Session(engine) as db:
        assert db.get(DmInbox, (aid, "555")).cursor == "older"
        dm_intake.poll_inbox(aid, db)
    restarted = Executor(engine, Adapter)
    assert restarted.run_account(aid) and restarted.run_account(aid)
    with Session(engine) as db:
        inbox = db.get(DmInbox, (aid, "555"))
        assert inbox.cursor is None and inbox.watermark == "4"
        dm_intake.poll_inbox(aid, db)
    assert restarted.run_account(aid) and not restarted.run_account(aid)
    assert control.pages == [None, "older", None] and control.imports == ["700", "702"]
    assert not control.writes

    with Session(engine) as db:
        submissions = db.scalars(
            select(DmSubmission).order_by(DmSubmission.message_id.desc())
        ).all()
        drafts = [db.get(ContentDraft, item.draft_id) for item in submissions]
        assert len(drafts) == 2 and all(draft.status == "draft" for draft in drafts)
        assert [draft.caption for draft in drafts] == ["chip em", "Incoming photo"]
        assert dm_intake.retry_import(submissions[0].id, db).id == submissions[0].job_id
        assert drafts[0].asset_id == drafts[1].asset_id
        asset = db.get(ContentAsset, drafts[0].asset_id)
        assert asset.storage_backend == storage
        assert read_original(config, asset) == original
        feed = drafts[0].id
        with Image.open(io.BytesIO(content_drafts.preview(feed, 1, db).body)) as image:
            assert image.size == (320, 200)
        with pytest.raises(HTTPException, match="verified published photo"):
            create_story_draft(feed, StoryDraftCreate(expected_revision=1), db)
        feed_job = content_publication.approve_publication(
            feed, content_publication.PublicationApproval(expected_revision=1, reviewed=True), db
        ).id
    assert restarted.run_account(aid)
    with Session(engine) as db:
        assert (
            db.get(Job, feed_job).state == "succeeded"
            and db.get(ContentDraft, feed).status == "published"
        )
        caption_job = content_publication.approve_media_change(
            feed,
            content_publication.MediaApproval(
                expected_revision=1,
                reviewed=True,
                operation="edit_caption",
                caption="Reviewed feed caption",
                idempotency_key="test-dm-caption",
            ),
            db,
        ).id
    assert restarted.run_account(aid)
    with Session(engine) as db:
        job = db.get(Job, caption_job)
        assert job.state == "succeeded", job.error
        assert db.get(ContentDraft, feed).caption == "Reviewed feed caption"
        with pytest.raises(HTTPException, match="photo post changed"):
            create_story_draft(feed, StoryDraftCreate(expected_revision=1), db)
        companion = create_story_draft(feed, StoryDraftCreate(expected_revision=2), db)
        story = companion.id
        assert companion.source_draft_id == feed
        assert companion.asset_id == db.get(ContentDraft, feed).asset_id
        assert companion.kind == "story_photo" and companion.caption == ""
        assert create_story_draft(feed, StoryDraftCreate(expected_revision=2), db).id == story
        assert control.writes == ["configure_photo", "edit_caption"]
        with pytest.raises(HTTPException, match="linked to a published photo"):
            edit_draft(story, DraftPatch(expected_revision=1, kind="feed_photo"), db)
        edit_draft(
            story, DraftPatch(expected_revision=1, kind="story_photo", overlay_text="Reviewed"), db
        )
        with Image.open(io.BytesIO(content_drafts.preview(story, 2, db).body)) as image:
            assert image.size == (1080, 1920)
        story_job = content_publication.approve_publication(
            story, content_publication.PublicationApproval(expected_revision=2, reviewed=True), db
        ).id
    assert restarted.run_account(aid)
    with Session(engine) as db:
        assert (
            db.get(Job, story_job).state == "succeeded"
            and db.get(ContentDraft, story).status == "published"
        )
    assert control.writes == ["configure_photo", "edit_caption", "configure_story"]

    def generate(model, vision, image, source, examples, *, report, observe, ocr_image):
        assert (model, vision) == ("test-model", "test-vision")
        assert image and source == "Incoming photo"
        assert examples == ["A study in color and form."]
        if not writer_available:
            report("result", {"state": "failed", "reason": "no_context"})
            return None
        report("result", {"state": "complete"})
        return "a plain green frame , with an even wash of color."

    monkeypatch.setattr(dm_intake, "write_caption", generate)
    with Session(engine) as db:
        inbox = db.get(DmInbox, (aid, "555"))
        config.dm_default_account_id = aid
        config.dm_default_from = at - timedelta(seconds=1)
        config.dm_poll_interval_seconds = 60
        config.dm_caption_model = "test-model"
        config.dm_caption_vision_model = "test-vision"
        config.dm_caption_sources = "reference_account"
        config.dm_spotify_playlist_id = "1234567890123456789012"
        db.add(
            PlaylistTrack(
                playlist_id=config.dm_spotify_playlist_id,
                spotify_id="0987654321098765432109",
                title="Fixture Song",
                artist="Fixture Artist",
            )
        )
        observation = Job(
            account_id=aid, kind="insight", state="succeeded", idempotency_key="style-example"
        )
        db.add(observation)
        db.flush()
        db.add(
            InsightCheck(
                account_id=aid,
                job_id=observation.id,
                kind="posts",
                target="reference_account",
                items=[{"caption": "A study in color and form."}],
                observed_at=at,
            )
        )
        inbox.direct_publish_from = config.dm_default_from
        share = DmMessage("5", "555", at, "703", None)
        dm_intake.record_page(db, inbox, "123", None, [share], at)
        dm_intake.record_page(db, inbox, "123", None, [share], at + timedelta(seconds=60))
        db.commit()

    after_grace = Executor(engine, Adapter, clock=lambda: at + timedelta(seconds=80))
    assert after_grace.run_account(aid) and after_grace.run_account(aid)
    with Session(engine) as db:
        submission = db.scalar(select(DmSubmission).where(DmSubmission.message_id == "5"))
        draft = db.get(ContentDraft, submission.draft_id)
        assert submission.caption_command["state"] == "generated"
        assert draft.status == "published" and draft.kind == "feed_photo"
        expected = (
            "a plain green frame , with an even wash of color."
            if writer_available
            else caption_writer.archive_caption("Incoming photo", "703")
        )
        assert draft.caption == control.caption == expected
        selection = db.get(PlaylistSelection, draft.id)
        assert selection.catalog_id is None and draft.music_canonical_id is None
        assert selection.audio_asset_id == draft.playlist_music["audio_asset_id"] == "5555"
        assert draft.playlist_music == control.music
        job = db.get(Job, submission.job_id)
        assert job.payload["caption_run"]["stages"]["result"]["state"] == "complete"
        if not writer_available:
            assert job.payload["caption_run"]["stages"]["fallback"]["reason"] == "no_context"
        assert job.payload["music_checks"][0]["state"] == "matched"
        assert not available_tracks(db, aid, config.dm_spotify_playlist_id)
        assert control.writes[-1] == "configure_photo"
