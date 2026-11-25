"""The diagnostic report must not expose decrypted session material."""

import json
import runpy
from pathlib import Path


def test_session_summary_only_exposes_profile_and_presence_flags(monkeypatch):
    monkeypatch.setenv("INTERNAL_API_TOKEN", "offline-sanity-test-token-at-least-24")
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    module = runpy.run_path(str(Path(__file__).resolve().parents[1] / "account_sanity.py"))
    summary = module["session_summary"](
        {
            "device_settings": {
                "app_version": "449.0",
                "model": "Pixel 8 Pro",
                "unexpected_secret": "private-profile",
            },
            "authorization_data": {"ds_user_id": "private-id", "authorization": "private-bearer"},
            "cookies": {"sessionid": "private-cookie", "csrftoken": "private-csrf"},
            "uuids": {
                "uuid": "private-uuid",
                "phone_id": "private-phone",
                "android_device_id": "private-device",
            },
            "context_data": "private-context",
            "password": "private-password",
            "fbns_auth": {"token": "private-push"},
        }
    )
    encoded = json.dumps(summary)
    assert "private-" not in encoded
    assert summary["has_authenticated_material"] and summary["has_device_identifiers"]
    assert summary["device_profile"]["app_version"] == "449.0"
