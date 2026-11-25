"""Verify the configured private R2 bucket using a temporary fixture."""

import io
import uuid
from types import SimpleNamespace

from app.config import settings
from app.content_images import render_image, validate_image
from app.content_storage import (
    bucket_args,
    read_original,
    upload_backend,
    write_bucket_original,
)
from botocore import UNSIGNED
from botocore.config import Config
from botocore.exceptions import ClientError
from botocore.session import get_session
from PIL import Image


def main():
    config = settings()
    if upload_backend(config) != "r2":
        raise SystemExit("Configure private Cloudflare R2 before running this check")
    stream = io.BytesIO()
    Image.new("RGB", (96, 128), "green").save(stream, "PNG")
    original = stream.getvalue()
    asset = SimpleNamespace(
        id=str(uuid.uuid4()),
        account_id=str(uuid.uuid4()),
        storage_backend="r2",
        **validate_image(original),
    )
    client, args = bucket_args(config, asset)
    anonymous = get_session().create_client(
        "s3",
        endpoint_url=config.content_r2_endpoint,
        region_name="auto",
        config=Config(
            signature_version=UNSIGNED,
            s3={"addressing_style": "path"},
            retries={"total_max_attempts": 1},
            connect_timeout=5,
            read_timeout=10,
        ),
    )
    try:
        write_bucket_original(config, asset, original)
        write_bucket_original(config, asset, original)
        assert read_original(config, asset) == original
        preview = render_image(
            config, asset, {"kind": "feed_photo", "fit_mode": "fit", "rotation": 0}
        )
        with Image.open(io.BytesIO(preview)) as image:
            assert image.size == (1080, 1350)
        try:
            response = anonymous.get_object(**args)
        except ClientError as exc:
            assert exc.response["ResponseMetadata"]["HTTPStatusCode"] in {400, 401, 403}
        else:
            response["Body"].close()
            raise RuntimeError("Unsigned R2 access unexpectedly succeeded")
    finally:
        client.delete_object(**args)
    print(
        "PASS: private R2 write, immutable reuse, verified original, preview and unsigned denial"
    )


if __name__ == "__main__":
    main()
