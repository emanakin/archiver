import io
import uuid
from types import SimpleNamespace

import pytest
from botocore.exceptions import ClientError
from PIL import Image
from sqlalchemy import select

from app import content_drafts, content_storage
from app.content_backup import export_originals, restore_originals
from app.content_images import asset_path, render_image, validate_image
from app.execution_errors import OperationBlocked
from app.models import ContentAsset, ContentDraft, Job


def image_bytes(format="PNG"):
    buffer = io.BytesIO()
    Image.new("RGB", (160, 100), (30, 100, 60)).save(buffer, format=format)
    return buffer.getvalue()


@pytest.mark.parametrize("kind,size", [("feed_photo", (640, 480)), ("story_photo", (1080, 1920))])
def test_original_frame_preserves_image_proportions_and_fills_edges(tmp_path, kind, size):
    original = io.BytesIO()
    with Image.new("RGB", (640, 480), (230, 80, 42)) as source:
        source.save(original, "PNG")
    data = original.getvalue()
    asset = SimpleNamespace(id=str(uuid.uuid4()), storage_backend="local", **validate_image(data))
    asset_path(tmp_path, asset.id).write_bytes(data)
    config = SimpleNamespace(content_store_path=tmp_path)
    values = {"kind": kind, "fit_mode": "native", "rotation": 0}
    with Image.open(io.BytesIO(render_image(config, asset, values))) as preview:
        assert preview.size == size
        reference = preview.getpixel((size[0] // 2, size[1] // 2))
        assert reference[0] > 200
        assert preview.getpixel((0, 0)) == reference
        assert preview.getpixel((size[0] - 1, size[1] - 1)) == reference
    assert asset_path(tmp_path, asset.id).read_bytes() == data


def test_upload_draft_revision_and_exact_preview_are_local_only(system, monkeypatch, tmp_path):
    client, db, account, _ = system
    account.mode = "live"
    db.commit()
    monkeypatch.setattr(
        content_drafts, "settings", lambda: SimpleNamespace(content_store_path=tmp_path)
    )
    raw = image_bytes()
    asset = client.post(
        f"/api/v1/accounts/{account.id}/content-assets",
        content=raw,
        headers={"Content-Type": "image/png"},
    )
    assert asset.status_code == 201 and asset.json()["width"] == 160
    asset_id = asset.json()["id"]
    assert (
        client.post(f"/api/v1/accounts/{account.id}/content-assets", content=raw).json()["id"]
        == asset_id
    )
    assert asset_path(tmp_path, asset_id).read_bytes() == raw
    body = {
        "account_id": account.id,
        "asset_id": asset_id,
        "kind": "story_photo",
        "overlay_text": "An owner draft",
        "idempotency_key": str(uuid.uuid4()),
    }
    result = client.post("/api/v1/content-drafts", json=body)
    assert result.status_code == 201
    draft = result.json()
    assert client.post("/api/v1/content-drafts", json=body).json()["id"] == draft["id"]
    preview = client.get(draft["preview_url"])
    assert preview.status_code == 200 and preview.headers["content-type"] == "image/jpeg"
    with Image.open(io.BytesIO(preview.content)) as rendered:
        assert rendered.size == (1080, 1920)
    patch = {
        "expected_revision": 1,
        "kind": "feed_photo",
        "rotation": 90,
        "fit_mode": "fill",
        "caption": "Saved caption",
    }
    saved = client.patch(f"/api/v1/content-drafts/{draft['id']}", json=patch)
    assert saved.json()["revision"] == 2 and saved.json()["caption"] == "Saved caption"
    assert client.get(draft["preview_url"]).status_code == 409
    assert client.patch(f"/api/v1/content-drafts/{draft['id']}", json=patch).status_code == 409
    assert asset_path(tmp_path, asset_id).read_bytes() == raw
    assert client.get(f"/api/v1/content-drafts?account_id={account.id}").json()["total"] == 1
    assert db.scalar(select(Job)) is None
    db.get(ContentDraft, draft["id"]).status = "queued"
    db.commit()
    assert (
        client.patch(
            f"/api/v1/content-drafts/{draft['id']}", json={**patch, "expected_revision": 2}
        ).status_code
        == 409
    )


@pytest.mark.parametrize("data", [b"not-an-image", image_bytes("GIF")])
def test_invalid_images_never_enter_asset_store(system, monkeypatch, tmp_path, data):
    client, db, account, _ = system
    account.mode = "live"
    db.commit()
    monkeypatch.setattr(
        content_drafts, "settings", lambda: SimpleNamespace(content_store_path=tmp_path)
    )
    assert (
        client.post(f"/api/v1/accounts/{account.id}/content-assets", content=data).status_code
        == 422
    )
    assert db.scalar(select(ContentAsset)) is None and list(tmp_path.iterdir()) == []


def test_asset_paths_and_image_size_are_bounded(tmp_path):
    with pytest.raises(ValueError):
        asset_path(tmp_path, "../other-file")
    with pytest.raises(OperationBlocked):
        validate_image(b"x" * (8 * 1024 * 1024 + 1))


@pytest.mark.parametrize("backend", ["s3", "r2"])
def test_bucket_upload_preview_and_checksum_verified_restore(
    system, monkeypatch, tmp_path, backend
):
    client, db, account, _ = system
    account.mode = "live"
    db.commit()
    secret = tmp_path / "credentials.json"
    secret.touch()
    endpoint = "https://" + "a" * 32 + ".r2.cloudflarestorage.com"
    config = SimpleNamespace(
        content_storage_backend=backend,
        content_bucket="private",
        content_store_path=tmp_path / "originals",
        content_s3_endpoint=None,
        content_s3_region="us-east-1",
        content_s3_credentials_file=None,
        content_r2_bucket="private",
        content_r2_endpoint=endpoint,
        content_r2_credentials_file=str(secret),
    )
    data = {}

    class Bucket:
        def put_object(self, *, Bucket, Key, Body, ContentType, IfNoneMatch):
            assert not db.in_transaction()
            assert Bucket == "private" and IfNoneMatch == "*" and ContentType == "image/png"
            if Key in data:
                raise ClientError({"ResponseMetadata": {"HTTPStatusCode": 412}}, "PutObject")
            data[Key] = Body

        def get_object(self, *, Bucket, Key):
            if Key not in data:
                raise ClientError({"ResponseMetadata": {"HTTPStatusCode": 404}}, "GetObject")
            return {"Body": io.BytesIO(data[Key])}

    bucket = Bucket()
    monkeypatch.setattr(content_drafts, "settings", lambda: config)

    def connect(actual_endpoint, region, credentials_file):
        if backend == "r2":
            assert (actual_endpoint, region, credentials_file) == (endpoint, "auto", str(secret))
        return bucket

    monkeypatch.setattr(content_storage, "s3_client", connect)
    raw = image_bytes()
    url = f"/api/v1/accounts/{account.id}/content-assets"
    result = client.post(url, content=raw)
    assert result.status_code == 201
    asset_id = result.json()["id"]
    assert client.post(url, content=raw).json()["id"] == asset_id
    asset = db.get(ContentAsset, asset_id)
    db.expunge(asset)
    db.rollback()
    assert asset.storage_backend == backend and not config.content_store_path.exists()
    response = client.post(
        "/api/v1/content-drafts",
        json={
            "account_id": account.id,
            "asset_id": asset_id,
            "kind": "story_photo",
            "idempotency_key": str(uuid.uuid4()),
        },
    )
    draft = response.json()
    assert response.status_code == 201
    preview = client.get(draft["preview_url"])
    with Image.open(io.BytesIO(preview.content)) as image:
        assert image.size == (1080, 1920)
    archive = tmp_path / "backup.tar"
    export_originals(archive, config, [asset])
    key = content_storage.object_key(asset)
    data[key] = b"corrupted"
    assert client.get(draft["preview_url"]).status_code == 503
    db.rollback()
    with pytest.raises(content_storage.StorageUnavailable):
        restore_originals(archive, config, [asset])
    del data[key]
    restore_originals(archive, config, [asset])
    assert data[key] == raw and client.get(draft["preview_url"]).status_code == 200


def test_r2_upload_holds_without_credentials_and_never_falls_back(system, monkeypatch, tmp_path):
    client, db, account, _ = system
    account.mode = "live"
    db.commit()
    config = SimpleNamespace(
        content_storage_backend="r2",
        content_bucket="legacy",
        content_store_path=tmp_path,
        content_r2_bucket="",
        content_r2_endpoint="",
        content_r2_credentials_file=None,
    )
    monkeypatch.setattr(content_drafts, "settings", lambda: config)
    result = client.post(f"/api/v1/accounts/{account.id}/content-assets", content=image_bytes())
    assert result.status_code == 503
    assert db.scalar(select(ContentAsset)) is None and not list(tmp_path.iterdir())


def test_carousel_revision_and_discard_preserve_originals(system, monkeypatch, tmp_path):
    client, db, account, _ = system
    account.mode = "live"
    db.commit()
    monkeypatch.setattr(
        content_drafts, "settings", lambda: SimpleNamespace(content_store_path=tmp_path)
    )
    url = f"/api/v1/accounts/{account.id}/content-assets"
    images = [client.post(url, content=image_bytes(kind)).json()["id"] for kind in ("PNG", "JPEG")]
    draft = ContentDraft(
        account_id=account.id,
        kind="feed_carousel",
        asset_id=images[0],
        photo_assets=images,
        photo_order=[1, 2],
    )
    db.add(draft)
    db.commit()
    path = f"/api/v1/content-drafts/{draft.id}"
    body = {"expected_revision": 1, "kind": "feed_carousel", "caption": "Example"}
    assert client.patch(path, json={**body, "photo_order": [1, 1]}).status_code == 422
    updated = client.patch(path, json={**body, "photo_order": [2, 1]}).json()
    assert [photo["number"] for photo in updated["photos"]] == [2, 1]
    assert client.get(path + "/preview?revision=1&photo=2").status_code == 409
    assert client.get(updated["photos"][0]["preview_url"]).status_code == 200
    assert client.get(path + "/preview?revision=2&photo=3").status_code == 404
    assert client.delete(path + "?revision=1").status_code == 409
    draft.status = "published"
    db.commit()
    assert client.delete(path + "?revision=2").status_code == 409
    draft.status = "deleted"
    db.commit()
    assert client.delete(path + "?revision=2").status_code == 204
    assert client.delete(path + "?revision=2").status_code == 204
    assert client.get(path).status_code == 404
    assert client.get(f"/api/v1/content-drafts?account_id={account.id}").json()["total"] == 0
    assert all(asset_path(tmp_path, asset).is_file() for asset in images)
