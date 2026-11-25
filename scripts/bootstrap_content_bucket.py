"""Run with the app image on the private bucket network and both keys mounted."""

import io
import tempfile
import uuid
from pathlib import Path
from types import SimpleNamespace

from botocore import UNSIGNED
from botocore.config import Config
from botocore.exceptions import ClientError
from botocore.session import get_session
from PIL import Image

from app.content_backup import export_originals, restore_originals
from app.content_images import render_image, validate_image
from app.content_storage import object_key, read_original, s3_client, write_bucket_original


def main():
    config = SimpleNamespace(
        content_bucket="archive-content", content_s3_endpoint="http://content-bucket:8333",
        content_s3_region="us-east-1", content_s3_credentials_file="/run/secrets/app",
    )
    admin = s3_client(config.content_s3_endpoint, config.content_s3_region, "/run/secrets/admin")
    if config.content_bucket not in {item["Name"] for item in admin.list_buckets()["Buckets"]}:
        admin.create_bucket(Bucket=config.content_bucket)

    stream = io.BytesIO()
    Image.new("RGB", (96, 128), "green").save(stream, "PNG")
    original = stream.getvalue()
    asset = SimpleNamespace(
        id=str(uuid.uuid4()), account_id=str(uuid.uuid4()), storage_backend="s3",
        **validate_image(original),
    )
    args = {"Bucket": config.content_bucket, "Key": object_key(asset)}
    anonymous = get_session().create_client(
        "s3", endpoint_url=config.content_s3_endpoint, region_name=config.content_s3_region,
        config=Config(signature_version=UNSIGNED, s3={"addressing_style": "path"}),
    )
    try:
        write_bucket_original(config, asset, original)
        write_bucket_original(config, asset, original)
        preview = render_image(config, asset, {"kind": "story_photo", "fit_mode": "fit", "rotation": 0})
        with Image.open(io.BytesIO(preview)) as image:
            assert image.size == (1080, 1920)
        try:
            anonymous.get_object(**args)
        except ClientError as exc:
            assert exc.response["ResponseMetadata"]["HTTPStatusCode"] in {401, 403}
        else:
            raise AssertionError("Anonymous bucket read unexpectedly succeeded")
        with tempfile.TemporaryDirectory() as tmp:
            tar = Path(tmp) / "content.tar"
            export_originals(tar, config, [asset])
            admin.delete_object(**args)
            restore_originals(tar, config, [asset])
            assert read_original(config, asset) == original
    finally:
        admin.delete_object(**args)
    print("PASS: private bucket upload, image preview, anonymous denial, and checksum-verified backup and restore")


if __name__ == "__main__":
    main()
