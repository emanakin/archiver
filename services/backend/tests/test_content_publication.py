import io
import os
import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import content_publication as content
from app.content_images import asset_path, validate_image
from app.execution_errors import OperationBlocked, TransientRead, UncertainWrite
from app.executor import Executor
from app.models import (
    Account,
    AccountSettings,
    ActionAttempt,
    AuditEvent,
    ContentAsset,
    ContentDraft,
    Job,
    now,
)
from app.schemas import SettingsValues


def draft_fixture(db, aid, root, kind="feed_photo"):
    account = db.get(Account, aid)
    account.mode, account.session_state, account.instagram_id = "live", "connected", "999"
    account.read_only_verified_at = now()
    data = io.BytesIO()
    Image.new("RGB", (160, 100), (90, 120, 70)).save(data, "PNG")
    raw = data.getvalue()
    asset = ContentAsset(account_id=aid, **validate_image(raw))
    db.add(asset)
    db.flush()
    asset_path(root, asset.id).write_bytes(raw)
    draft = ContentDraft(account_id=aid, asset_id=asset.id, kind=kind, caption="Owner reviewed")
    db.add(draft)
    db.add(AccountSettings(account_id=aid, values=SettingsValues(request_ceiling=100).model_dump()))
    db.commit()
    return draft.id


def test_archive_requires_exact_linked_replacement_and_playback(system, monkeypatch, tmp_path):
    client, db, account, _ = system
    original = db.get(ContentDraft, draft_fixture(db, account.id, tmp_path))
    monkeypatch.setattr(content, "settings", lambda: SimpleNamespace(adapter_mode="live"))
    original.status, original.media_id = "published", "123_999"
    replacement = ContentDraft(
        account_id=account.id,
        asset_id=original.asset_id,
        kind=original.kind,
        caption="New caption",
        media_id="321_999",
        status="published",
        music_canonical_id="98",
    )
    db.add(replacement)
    db.flush()
    publish = Job(
        account_id=account.id,
        idempotency_key="replacement",
        kind="publish_photo",
        state="succeeded",
        payload={"snapshot": content.snapshot(replacement)},
    )
    db.add(publish)
    db.flush()
    replacement.publication_job_id = publish.id
    db.commit()
    route = f"/api/v1/content-drafts/{original.id}/archive-replaced"
    body = {"expected_revision": 1, "replacement_id": replacement.id, "playback_confirmed": True}
    assert client.post(route, json=body).status_code == 409
    db.add(
        AuditEvent(
            account_id=account.id,
            event="photo_replacement_prepared",
            detail={"original_id": original.id, "replacement_id": replacement.id},
        )
    )
    db.commit()
    assert client.post(route, json={**body, "playback_confirmed": False}).status_code == 422
    result = client.post(route, json=body)
    assert result.status_code == 202
    assert result.json()["kind"] == "archive_post"
    assert db.get(Job, result.json()["id"]).payload["media_id"] == "123_999"


@pytest.mark.skipif(not os.getenv("TEST_POSTGRES_URL"), reason="Requires PostgreSQL account lane")
def test_ordered_carousel_uses_cover_frame_and_verifies_child_receipts(
    postgres, monkeypatch, tmp_path
):
    engine, aid = postgres
    monkeypatch.setattr(
        content,
        "settings",
        lambda: SimpleNamespace(adapter_mode="live", content_store_path=tmp_path),
    )
    with Session(engine) as db:
        did = draft_fixture(db, aid, tmp_path, "feed_carousel")
        draft = db.get(ContentDraft, did)
        data = io.BytesIO()
        with Image.new("RGB", (100, 100), (200, 40, 30)) as photo:
            photo.save(data, "PNG")
        raw = data.getvalue()
        cover = ContentAsset(account_id=aid, **validate_image(raw))
        db.add(cover)
        db.flush()
        asset_path(tmp_path, cover.id).write_bytes(raw)
        draft.photo_assets = [draft.asset_id, cover.id]
        draft.photo_order = [2, 1]
        db.commit()
        job = content.approve_publication(
            did, content.PublicationApproval(expected_revision=1, reviewed=True), db
        )
        assert job.payload["asset_ids"] == [cover.id, draft.asset_id]
        jid = job.id

    class Adapter:
        def __init__(self, guard):
            self.guard = guard

        def publish_carousel(self, paths, parent, children, values):
            for path, child, red_cover in zip(paths, children, [True, False], strict=True):
                with Image.open(path) as photo:
                    assert photo.size == (320, 320)
                    assert (photo.getpixel((160, 160))[0] > 150) == red_cover
                self.guard.before_request(
                    media={"operation": "upload", "sidecar": True, "upload_id": child}
                )
            self.guard.before_request(
                media={
                    "operation": "configure_carousel",
                    "upload_id": parent,
                    "child_upload_ids": children,
                    "client_sidecar_id": parent,
                    "caption": values["caption"],
                }
            )
            return {"media_id": "987_999", "child_media_ids": ["91", "92"]}

        def owned_media(self, media_id, actor):
            assert actor == "999"
            self.guard.before_request()
            return {"media_id": media_id, "kind": "feed_carousel", "child_media_ids": ["91", "92"]}

    executor = Executor(engine, Adapter)
    assert executor.run_account(aid)
    with Session(engine) as db:
        assert db.get(Job, jid).state == "succeeded"
        assert db.get(ContentDraft, did).status == "published"


def test_publication_requires_live_transport_review_and_exact_revision(
    system, monkeypatch, tmp_path
):
    client, db, account, _ = system
    did = draft_fixture(db, account.id, tmp_path)
    monkeypatch.setattr(content, "settings", lambda: SimpleNamespace(adapter_mode="read_only"))
    assert (
        client.post(
            f"/api/v1/content-drafts/{did}/publish", json={"expected_revision": 1, "reviewed": True}
        ).status_code
        == 409
    )
    monkeypatch.setattr(content, "settings", lambda: SimpleNamespace(adapter_mode="live"))
    assert (
        client.post(
            f"/api/v1/content-drafts/{did}/publish",
            json={"expected_revision": 1, "reviewed": False},
        ).status_code
        == 422
    )
    assert (
        client.post(
            f"/api/v1/content-drafts/{did}/publish", json={"expected_revision": 2, "reviewed": True}
        ).status_code
        == 409
    )
    body = {"expected_revision": 1, "reviewed": True}
    result = client.post(f"/api/v1/content-drafts/{did}/publish", json=body)
    assert result.status_code == 202
    assert (
        client.post(f"/api/v1/content-drafts/{did}/publish", json=body).json()["id"]
        == result.json()["id"]
    )
    assert len(db.scalars(select(Job)).all()) == 1
    assert (
        client.patch(
            f"/api/v1/content-drafts/{did}", json={"expected_revision": 1, "caption": "Changed"}
        ).status_code
        == 409
    )
    assert (
        client.post(
            f"/api/v1/jobs/{result.json()['id']}/retry", json={"idempotency_key": str(uuid.uuid4())}
        ).status_code
        == 409
    )
    assert client.post(f"/api/v1/jobs/{result.json()['id']}/cancel", json={}).status_code == 200
    db.expire_all()
    assert db.get(ContentDraft, did).status == "cancelled"


def test_account_hold_surfaces_outside_recent_history(system):
    client, db, account, _ = system
    at = now()
    blocker = Job(
        account_id=account.id,
        kind="publish_carousel",
        idempotency_key="older-upload",
        created_at=at - timedelta(days=1),
        state="unknown",
        dispatch_started_at=at,
        payload={
            "draft_id": "original",
            "verification_detail": "Instagram did not confirm the approved music.",
        },
    )
    db.add(blocker)
    db.add_all(
        Job(
            account_id=account.id,
            kind="reconcile_content",
            state="failed",
            idempotency_key=f"read-{i}",
            created_at=at,
        )
        for i in range(100)
    )
    db.commit()
    history = client.get("/api/v1/content-operations", params={"account_id": account.id}).json()
    assert len(history["items"]) == 100
    assert all(job["id"] != blocker.id for job in history["items"])
    assert history["blocking_job"]["payload"] == blocker.payload


@pytest.mark.parametrize(
    "reference",
    [
        "file:///etc/passwd",
        "https://evil.example/p/ABC/",
        "https://www.instagram.com.evil.example/p/ABC/",
        "../1",
        "https://www.instagram.com/p/ABC/?token=private",
    ],
)
def test_reconciliation_rejects_untrusted_media_references(reference):
    with pytest.raises(ValueError):
        content.ReconcileApproval(job_id="test", media_ref=reference)


def test_cancelled_caption_approval_can_be_replaced_with_a_new_explicit_intent(
    system, monkeypatch, tmp_path
):
    client, db, account, _ = system
    did = draft_fixture(db, account.id, tmp_path)
    draft = db.get(ContentDraft, did)
    draft.status, draft.media_id = "published", "987_999"
    db.commit()
    monkeypatch.setattr(content, "settings", lambda: SimpleNamespace(adapter_mode="live"))
    body = {
        "expected_revision": 1,
        "reviewed": True,
        "operation": "edit_caption",
        "caption": "First caption",
        "idempotency_key": str(uuid.uuid4()),
    }
    original = client.post(f"/api/v1/content-drafts/{did}/media-action", json=body)
    assert original.status_code == 202
    assert client.post(f"/api/v1/jobs/{original.json()['id']}/cancel", json={}).status_code == 200
    replacement = client.post(
        f"/api/v1/content-drafts/{did}/media-action",
        json={**body, "caption": "Replacement", "idempotency_key": str(uuid.uuid4())},
    )
    assert replacement.status_code == 202 and replacement.json()["id"] != original.json()["id"]


@pytest.mark.skipif(not os.getenv("TEST_POSTGRES_URL"), reason="Requires PostgreSQL account lane")
@pytest.mark.parametrize(
    "outcome",
    [
        "success",
        "timeout",
        "receipt_read_fail",
        "readonly",
        "changed",
        "wrong_caption",
        "proof_revoked",
    ],
)
def test_one_shot_publication_and_reconciliation(postgres, monkeypatch, tmp_path, outcome):
    engine, aid = postgres
    monkeypatch.setattr(
        content,
        "settings",
        lambda: SimpleNamespace(adapter_mode="live", content_store_path=tmp_path),
    )
    with Session(engine) as db:
        did = draft_fixture(db, aid, tmp_path)
        job_id = content.approve_publication(
            did, content.PublicationApproval(expected_revision=1, reviewed=True), db
        ).id
        if outcome == "changed":
            db.get(ContentDraft, did).caption = "Changed after approval"
            db.commit()
        if outcome == "proof_revoked":
            db.get(Account, aid).read_only_verified_at = None
            db.commit()
    control = SimpleNamespace(writes=0, uploads=0, fail_read=outcome == "receipt_read_fail")

    class Adapter:
        def __init__(self, guard):
            self.guard = guard

        def publish_photo(self, path, upload_id, values):
            assert path.is_file()
            self.guard.before_request(media={"operation": "upload", "upload_id": upload_id})
            control.uploads += 1
            self.guard.before_request(
                media={
                    "operation": "configure_photo",
                    "upload_id": upload_id,
                    "caption": "Wrong" if outcome == "wrong_caption" else values["caption"],
                }
            )
            control.writes += 1
            if outcome == "timeout":
                raise UncertainWrite("Synthetic configure timeout")
            return {"media_id": "987_999"}

        def owned_media(self, media_id, actor):
            self.guard.before_request()
            assert actor == "999"
            if control.fail_read:
                raise TransientRead("Synthetic verification read failure")
            return {"media_id": "987_999", "caption": "Owner reviewed", "kind": "feed_photo"}

        def resolve_media_ref(self, reference):
            return reference

    executor = Executor(engine, Adapter, allow_mutations=outcome != "readonly")
    assert executor.run_account(aid)
    assert not executor.run_account(aid)
    with Session(engine) as db:
        job, draft = db.get(Job, job_id), db.get(ContentDraft, did)
        assert job.state == (
            "succeeded"
            if outcome == "success"
            else "unknown"
            if outcome in {"timeout", "receipt_read_fail"}
            else "blocked"
        )
        assert control.writes == (
            1 if outcome in {"success", "timeout", "receipt_read_fail"} else 0
        )
        assert draft.status == (
            "published"
            if outcome == "success"
            else "unknown"
            if job.state == "unknown"
            else "failed"
        )
        if outcome in {"timeout", "receipt_read_fail"}:
            if outcome == "receipt_read_fail":
                assert draft.media_id == "987_999"
            control.fail_read = False
            # Another revision/asset cannot evade an unresolved account hold.
            other = ContentDraft(account_id=aid, asset_id=draft.asset_id)
            db.add(other)
            db.commit()
            from fastapi import HTTPException

            with pytest.raises(HTTPException):
                content.approve_publication(
                    other.id, content.PublicationApproval(expected_revision=1, reviewed=True), db
                )
            db.rollback()
            content.request_reconciliation(
                did, content.ReconcileApproval(job_id=job_id, media_ref="987_999"), db
            )
    if outcome in {"timeout", "receipt_read_fail"}:
        assert executor.run_account(aid)
        with Session(engine) as db:
            assert db.get(Job, job_id).state == "succeeded"
            assert db.get(ContentDraft, did).status == "published"
            original = db.scalar(select(ActionAttempt).where(ActionAttempt.job_id == job_id))
            assert original.outcome == "unknown"  # append-only dispatch evidence retained
            assert control.writes == control.uploads == 1


@pytest.mark.skipif(not os.getenv("TEST_POSTGRES_URL"), reason="Requires PostgreSQL account lane")
def test_expired_content_claim_holds_publication_instead_of_replaying(
    postgres, monkeypatch, tmp_path
):
    engine, aid = postgres
    monkeypatch.setattr(content, "settings", lambda: SimpleNamespace(adapter_mode="live"))
    with Session(engine) as db:
        did = draft_fixture(db, aid, tmp_path)
        job = content.approve_publication(
            did, content.PublicationApproval(expected_revision=1, reviewed=True), db
        )
        job.state, job.dispatch_started_at, job.lease_expires_at = (
            "running",
            now() - timedelta(minutes=2),
            now() - timedelta(seconds=1),
        )
        db.add(
            ActionAttempt(
                job_id=job.id,
                account_id=aid,
                owner="old-owner",
                dispatch_started_at=job.dispatch_started_at,
            )
        )
        db.commit()
        Executor(engine, lambda guard: None).recover(db, aid)
        db.commit()
        assert job.state == "unknown" and db.get(ContentDraft, did).status == "unknown"


@pytest.mark.skipif(not os.getenv("TEST_POSTGRES_URL"), reason="Requires PostgreSQL account lane")
@pytest.mark.parametrize(
    "operation", ["edit_caption", "delete_story", "delete_post", "wrong_owner"]
)
def test_own_media_actions_verify_ownership_and_do_not_repeat(
    postgres, monkeypatch, tmp_path, operation
):
    engine, aid = postgres
    monkeypatch.setattr(
        content,
        "settings",
        lambda: SimpleNamespace(adapter_mode="live", content_store_path=tmp_path),
    )
    with Session(engine) as db:
        did = draft_fixture(
            db, aid, tmp_path, "story_photo" if operation == "delete_story" else "feed_photo"
        )
        draft = db.get(ContentDraft, did)
        draft.status, draft.media_id = "published", "987_999"
        db.commit()
        job_id = content.approve_media_change(
            did,
            content.MediaApproval(
                expected_revision=1,
                reviewed=True,
                idempotency_key=str(uuid.uuid4()),
                operation="edit_caption" if operation == "wrong_owner" else operation,
                caption="New caption",
            ),
            db,
        ).id
    control = SimpleNamespace(writes=0, caption="Owner reviewed")

    class Adapter:
        def __init__(self, guard):
            self.guard = guard

        def owned_media(self, mid, actor):
            self.guard.before_request()
            if operation == "wrong_owner":
                raise OperationBlocked("Synthetic ownership mismatch")
            return {
                "media_id": mid,
                "kind": "story_photo" if operation == "delete_story" else "feed_photo",
                "caption": control.caption,
            }

        def edit_caption(self, mid, caption, observation):
            self.guard.before_request(
                media={"operation": "edit_caption", "media_id": mid, "caption": caption}
            )
            control.writes += 1
            control.caption = caption

        def delete_story(self, mid):
            self.guard.before_request(media={"operation": "delete_story", "media_id": mid})
            control.writes += 1

        def delete_post(self, mid):
            self.guard.before_request(media={"operation": "delete_post", "media_id": mid})
            control.writes += 1

    executor = Executor(engine, Adapter)
    assert executor.run_account(aid)
    assert not executor.run_account(aid)
    with Session(engine) as db:
        job, draft = db.get(Job, job_id), db.get(ContentDraft, did)
        assert job.state == ("blocked" if operation == "wrong_owner" else "succeeded")
        assert control.writes == (0 if operation == "wrong_owner" else 1)
        if operation == "edit_caption":
            assert draft.caption == "New caption" and draft.revision == 2
        if operation in {"delete_story", "delete_post"}:
            assert draft.status == "deleted"
