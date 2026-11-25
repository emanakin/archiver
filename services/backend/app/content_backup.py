"""Export hash-verified originals from either storage backend to the backup tar."""

import argparse
import io
import os
import tarfile
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .content_storage import (
    MAX_BYTES,
    REMOTE_BACKENDS,
    asset_path,
    read_original,
    write_bucket_original,
)
from .content_video import MAX_VIDEO_BYTES
from .db import engine
from .models import ContentAsset


def assets():
    with Session(engine) as db:
        return db.scalars(select(ContentAsset).order_by(ContentAsset.id)).all()


def export_originals(path, config, originals):
    with tarfile.open(path, "x") as archive:
        for asset in originals:
            original = read_original(config, asset)
            entry = tarfile.TarInfo(f"{asset.id}.original")
            entry.mode, entry.size = 0o600, len(original)
            archive.addfile(entry, io.BytesIO(original))


def restore_originals(path, config, originals):
    # The restore drill verifies the manifest and DB hashes before this command.
    # Recheck each selected member and never extract a path from the archive.
    with tarfile.open(path) as archive:
        for asset in originals:
            entry = archive.getmember(f"{asset.id}.original")
            limit = MAX_VIDEO_BYTES if asset.mime == "video/mp4" else MAX_BYTES
            if not entry.isfile() or entry.size != asset.byte_size or not 0 < entry.size <= limit:
                raise ValueError("Invalid original in content backup")
            with archive.extractfile(entry) as source:
                original = source.read(asset.byte_size + 1)
            if len(original) != asset.byte_size or sha256(original).hexdigest() != asset.sha256:
                raise ValueError("Content backup checksum did not match")
            if asset.storage_backend in REMOTE_BACKENDS:
                write_bucket_original(config, asset, original)
            else:
                destination = asset_path(config.content_store_path, asset.id)
                destination.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
                with NamedTemporaryFile(dir=destination.parent, delete=False) as output:
                    temporary = Path(output.name)
                    output.write(original)
                    output.flush()
                    os.fsync(output.fileno())
                try:
                    os.link(temporary, destination)
                except FileExistsError:
                    read_original(config, asset)
                finally:
                    temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("export", "restore"))
    parser.add_argument("archive", type=Path)
    args = parser.parse_args()
    originals = list(assets())
    action = export_originals if args.action == "export" else restore_originals
    action(args.archive, settings(), originals)
    print(f"Verified {len(originals)} content originals")


if __name__ == "__main__":
    main()
