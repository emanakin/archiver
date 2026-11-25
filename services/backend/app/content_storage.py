"""Private storage for immutable originals shared by upload, preview and publication."""

import hashlib
import json
import os
import re
import uuid
from functools import lru_cache
from pathlib import Path

from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from botocore.session import get_session
from sqlalchemy import select

from .execution_errors import OperationBlocked
from .models import Account, ContentAsset

MAX_BYTES = 8 * 1024 * 1024
REMOTE_BACKENDS = {"s3", "r2"}
R2_ENDPOINT = re.compile(
    r"https://[0-9a-f]{32}(?:\.(?:eu|fedramp|us))?\.r2\.cloudflarestorage\.com/?"
)


class StorageUnavailable(OperationBlocked):
    code = "CONTENT_STORAGE_UNAVAILABLE"


def asset_path(directory, asset_id):
    return Path(directory).resolve() / f"{uuid.UUID(asset_id)}.original"


def object_key(asset):
    if not re.fullmatch(r"[0-9a-f]{64}", asset.sha256):
        raise StorageUnavailable("Original image has an invalid checksum")
    return f"accounts/{uuid.UUID(asset.account_id)}/originals/{asset.sha256}"


@lru_cache(maxsize=4)
def s3_client(endpoint, region, credentials_file):
    credentials = {}
    if credentials_file:
        with Path(credentials_file).open(encoding="utf-8") as source:
            secret = json.load(source)
        credentials = {
            "aws_access_key_id": secret["access_key_id"],
            "aws_secret_access_key": secret["secret_access_key"],
            "aws_session_token": secret.get("session_token"),
        }
    return get_session().create_client(
        "s3",
        endpoint_url=endpoint,
        region_name=region,
        config=Config(
            connect_timeout=5,
            read_timeout=10,
            retries={"total_max_attempts": 2, "mode": "standard"},
            request_checksum_calculation="when_required",
            s3={"addressing_style": "path"} if endpoint else None,
        ),
        **credentials,
    )


def bucket_args(config, asset):
    if asset.storage_backend == "r2":
        ensure_r2(config)
        client = s3_client(
            config.content_r2_endpoint.rstrip("/"), "auto", config.content_r2_credentials_file
        )
        return client, {"Bucket": config.content_r2_bucket, "Key": object_key(asset)}
    if not config.content_bucket:
        raise StorageUnavailable("Configure the private content bucket to read this image")
    client = s3_client(
        config.content_s3_endpoint or None,
        config.content_s3_region,
        config.content_s3_credentials_file,
    )
    return client, {"Bucket": config.content_bucket, "Key": object_key(asset)}


def ensure_r2(config):
    if (
        not config.content_r2_bucket
        or not R2_ENDPOINT.fullmatch(config.content_r2_endpoint)
        or not config.content_r2_credentials_file
        or not Path(config.content_r2_credentials_file).is_file()
    ):
        raise StorageUnavailable("Configure the private Cloudflare R2 bucket to save this image")


def upload_backend(config):
    backend = getattr(config, "content_storage_backend", None)
    if backend == "r2":
        ensure_r2(config)
    return backend or ("s3" if getattr(config, "content_bucket", "") else "local")


def read_original(config, asset):
    from .content_video import MAX_VIDEO_BYTES

    limit = MAX_VIDEO_BYTES if asset.mime == "video/mp4" else MAX_BYTES
    try:
        if asset.storage_backend in REMOTE_BACKENDS:
            client, args = bucket_args(config, asset)
            body = client.get_object(**args)["Body"]
            try:
                data = body.read(limit + 1)
            finally:
                body.close()
        else:
            with asset_path(config.content_store_path, asset.id).open("rb") as source:
                data = source.read(limit + 1)
    except (OSError, ValueError, KeyError, BotoCoreError, ClientError):
        raise StorageUnavailable("The saved image is temporarily unavailable") from None
    if (
        len(data) != asset.byte_size
        or len(data) > limit
        or hashlib.sha256(data).hexdigest() != asset.sha256
    ):
        raise StorageUnavailable("The original image does not match its saved asset")
    return data


def write_bucket_original(config, asset, data):
    try:
        client, args = bucket_args(config, asset)
        try:
            client.put_object(**args, Body=data, ContentType=asset.mime, IfNoneMatch="*")
        except ClientError as error:
            if error.response.get("ResponseMetadata", {}).get("HTTPStatusCode") != 412:
                raise
        # Verify both new objects and existing keys before making a DB reference.
        read_original(config, asset)
    except (OSError, ValueError, KeyError, BotoCoreError, ClientError):
        raise StorageUnavailable("The image could not be saved to the private bucket") from None


def persist_asset(db, config, account_id, data, metadata):
    """Persist a validated original, with remote I/O outside the DB transaction."""
    query = select(ContentAsset).where(
        ContentAsset.account_id == account_id, ContentAsset.sha256 == metadata["sha256"]
    )
    backend = upload_backend(config)
    existing = db.scalar(query)
    if existing and backend == "r2" and existing.storage_backend != "r2":
        raise StorageUnavailable("Copy the existing original to Cloudflare R2 before reusing it")
    if existing:
        return existing
    asset = ContentAsset(
        id=str(uuid.uuid4()),
        account_id=account_id,
        storage_backend=backend,
        **metadata,
    )
    if asset.storage_backend in REMOTE_BACKENDS:
        db.rollback()
        write_bucket_original(config, asset, data)

    db.scalar(select(Account).where(Account.id == account_id).with_for_update())
    existing = db.scalar(query)
    if existing and backend == "r2" and existing.storage_backend != "r2":
        raise StorageUnavailable("Copy the existing original to Cloudflare R2 before reusing it")
    if existing:
        return existing
    if asset.storage_backend == "local":
        path = asset_path(config.content_store_path, asset.id)
        created = False
        try:
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with path.open("xb") as output:
                created = True
                os.chmod(path, 0o600)
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
        except OSError:
            if created:
                path.unlink(missing_ok=True)
            raise StorageUnavailable("Image storage is temporarily unavailable") from None
    # Keep the object on uncertain DB commit. Bucket retries reuse its hash key.
    db.add(asset)
    db.flush()
    return asset
