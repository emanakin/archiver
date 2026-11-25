"""Archive the pinned committed source without changing the sibling checkout."""
from pathlib import Path
import hashlib
import json
import subprocess

root = Path(__file__).resolve().parents[1]
source = root.parent / "instagrapi"
pin = json.loads((root / "services/backend/app/instagram_pin.json").read_text())
actual = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
if actual != pin["commit"]:
    raise SystemExit("Instagrapi checkout differs from the approved pin; review the pin before packaging")
if subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=all"], cwd=source, text=True).strip():
    raise SystemExit("Instagrapi checkout has changes; committed source must be reviewed before packaging")
archive = subprocess.check_output(["git", "archive", "--format=zip", actual], cwd=source)
destination = root / "services/backend/vendor"
destination.mkdir(exist_ok=True)
(destination / "instagrapi-source.zip").write_bytes(archive)
manifest = {**pin, "archive_sha256": hashlib.sha256(archive).hexdigest()}
(destination / "instagram-build.json").write_text(json.dumps(manifest, indent=2) + "\n")
print("Packaged reviewed Instagrapi commit", actual, "from committed source")
