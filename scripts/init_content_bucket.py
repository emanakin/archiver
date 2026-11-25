"""Create private, non-overwriting credentials for the optional local bucket."""

import json
import os
import secrets
from pathlib import Path

root = Path(__file__).resolve().parents[1] / "secrets"


def write_private(path, value):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        json.dump(value, output, indent=2)


def main():
    root.mkdir(mode=0o700, exist_ok=True)
    app = root / "content_bucket_app.json"
    admin = root / "content_bucket_bootstrap.json"
    config = root / "content_bucket_identities.json"
    if all(path.is_file() for path in (app, admin, config)):
        print("Local bucket credentials already exist")
        return
    if any(path.exists() for path in (app, admin, config)):
        raise SystemExit("Partial credential set. Recover the matching files before proceeding")

    identities = []
    for name, path, actions in (
        ("archive-app", app, ["Read:archive-content", "Write:archive-content"]),
        ("archive-bootstrap", admin, ["Admin"]),
    ):
        access_key = secrets.token_hex(12)
        secret_key = secrets.token_urlsafe(32)
        write_private(path, {"access_key_id": access_key, "secret_access_key": secret_key})
        identities.append({
            "name": name,
            "credentials": [{"accessKey": access_key, "secretKey": secret_key}],
            "actions": actions,
        })
    write_private(config, {"identities": identities})
    print("Created local bucket credentials under secrets/")


if __name__ == "__main__":
    main()
