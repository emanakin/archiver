import hashlib
import os
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session
from test_content_publication import draft_fixture

from app import caption_pipeline as captions
from app import content_publication as content
from app import content_video
from app.content_storage import asset_path
from app.execution_errors import UncertainWrite
from app.executor import Executor
from app.models import ContentAsset, ContentDraft, Job, now


def test_validated_video_metadata_matches_private_storage(monkeypatch):
    original = b"\x00\x00\x00\x18ftyp" + b"example payload"
    monkeypatch.setattr(
        content_video, "probe_video", lambda _: {"width": 360, "height": 640, "duration": 8}
    )
    monkeypatch.setattr(content_video, "video_frame", lambda *_: b"cover")
    metadata = content_video.validate_video(original)
    assert metadata["mime"] == "video/mp4"
    assert metadata["byte_size"] == len(original)
    assert metadata["sha256"] == hashlib.sha256(original).hexdigest()


def test_editor_selects_grounded_candidate_and_rejects_archive_repeat(monkeypatch):
    best = "industrial design took a day off"
    calls = []

    def chat(model, messages, timeout, **options):
        calls.append(messages[-1]["content"])
        if len(calls) == 2:
            return "1", None
        return "\n".join(["a page from the archive", "the plastic years", best]), None

    monkeypatch.setattr(captions, "chat", chat)
    line, reason = captions.compose_editorial(
        "editor",
        "A translucent orange alarm clock found in Montreal.",
        "An orange plastic clock with an exposed circuit board.",
        ["SNOOZE"],
        "",
        ["The plastic years. #design"],
        [],
        [],
    )
    assert not reason
    assert line.startswith(best)
    assert '"candidates": {"1": "industrial design took a day off"}' in calls[-1]
    assert "exposed circuit board" in calls[-1]
    assert "SNOOZE" in calls[-1]


def test_reel_reconciliation_rejects_source_and_accepts_fresh_owner_link():
    dispatch = now()
    parent = SimpleNamespace(
        payload={"snapshot": {"caption": "My cut"}}, dispatch_started_at=dispatch
    )
    draft = SimpleNamespace(media_id=None, reel_source={"media_id": "12_456"})
    fresh = {
        "media_id": "34_456",
        "caption": "My cut",
        "media": SimpleNamespace(taken_at=dispatch + timedelta(seconds=10)),
    }
    content.verify_reconciled_reel(parent, draft, fresh)
    with pytest.raises(content.PublicationCheckFailed):
        content.verify_reconciled_reel(parent, draft, {**fresh, "media_id": "12_456"})
    with pytest.raises(content.PublicationCheckFailed):
        content.verify_reconciled_reel(
            parent,
            draft,
            {
                **fresh,
                "media": SimpleNamespace(taken_at=dispatch - timedelta(hours=1)),
            },
        )


def test_caption_worker_cannot_replace_an_owner_edit(system, tmp_path):
    _, db, account, _ = system
    draft_id = draft_fixture(db, account.id, tmp_path)
    draft = db.get(ContentDraft, draft_id)
    job = captions.enqueue_caption(db, draft, "A city archive")
    job_id, payload = job.id, dict(job.payload)
    job.state, job.claim_owner = "running", "editor-test"
    draft.caption = "Owner changed this during analysis"
    draft.caption_review = {**draft.caption_review, "state": "owner_caption"}
    draft.revision += 1
    db.commit()
    captions.finish_caption(
        db.get_bind(), job_id, account.id, payload, "editor-test", "The suggested line", None, None
    )
    db.expire_all()
    assert db.get(ContentDraft, draft_id).caption == "Owner changed this during analysis"
    assert db.get(Job, job_id).state == "cancelled"


@pytest.mark.skipif(not os.getenv("TEST_POSTGRES_URL"), reason="Requires PostgreSQL account lane")
@pytest.mark.parametrize("lost_response", [False, True])
def test_reel_publishes_once_and_keeps_uncertain_dispatch_held(
    postgres, monkeypatch, tmp_path, lost_response
):
    engine, aid = postgres
    original = b"video fixture"
    sha = hashlib.sha256(original).hexdigest()
    monkeypatch.setattr(
        content,
        "settings",
        lambda: SimpleNamespace(adapter_mode="live", content_store_path=tmp_path),
    )
    monkeypatch.setattr(content, "probe_video", lambda _: {"duration": 10})
    monkeypatch.setattr(content, "video_frame", lambda *_: b"cover")
    with Session(engine) as db:
        did = draft_fixture(db, aid, tmp_path, "feed_reel")
        draft = db.get(ContentDraft, did)
        asset = db.get(ContentAsset, draft.asset_id)
        asset.mime, asset.sha256, asset.byte_size = "video/mp4", sha, len(original)
        asset_path(tmp_path, asset.id).write_bytes(original)
        db.commit()
        with pytest.raises(HTTPException, match="Confirm permission"):
            content.approve_publication(
                did, content.PublicationApproval(expected_revision=1, reviewed=True), db
            )
        jid = content.approve_publication(
            did,
            content.PublicationApproval(
                expected_revision=1,
                reviewed=True,
                rights_confirmed=True,
            ),
            db,
        ).id

    dispatches = []

    class Adapter:
        def __init__(self, guard):
            self.guard = guard

        def publish_reel(self, video, cover, upload_id, info, caption):
            assert video.read_bytes() == original
            assert cover.read_bytes() == b"cover"
            session = content.reel_session_id("999", upload_id)
            self.guard.before_request(
                media={
                    "operation": "reel_settings",
                    "session": session,
                    "target_id": "999",
                }
            )
            for step in ("reel_init", "reel_video"):
                self.guard.before_request(
                    media={
                        "operation": step,
                        "session": session.replace("-", ""),
                        "upload_id": upload_id,
                        "size": len(original),
                        "sha256": sha,
                    }
                )
            self.guard.before_request(media={"operation": "upload", "upload_id": upload_id})
            self.guard.before_request(
                media={
                    "operation": "configure_reel",
                    "upload_id": upload_id,
                    "caption": caption,
                }
            )
            dispatches.append(upload_id)
            if lost_response:
                raise UncertainWrite("response lost")
            return {"media_id": "777_999"}

        def owned_media(self, media_id, actor):
            assert actor == "999"
            self.guard.before_request()
            return {"media_id": media_id, "kind": "feed_reel", "caption": "Owner reviewed"}

    worker = Executor(engine, Adapter)
    assert worker.run_account(aid)
    assert not worker.run_account(aid)
    assert len(dispatches) == 1
    with Session(engine) as db:
        job, draft = db.get(Job, jid), db.get(ContentDraft, did)
        assert job.dispatch_started_at
        assert job.state == ("unknown" if lost_response else "succeeded")
        assert draft.media_id == (None if lost_response else "777_999")
