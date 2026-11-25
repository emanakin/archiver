"""Validate only the local simulation stack. Never connects to Instagram."""
from pathlib import Path
import subprocess
import time
import uuid
import httpx
from dotenv import dotenv_values

root = Path(__file__).resolve().parents[1]
values = dotenv_values(root / ".env.integration", encoding="utf-8-sig")
base = values["APP_ORIGIN"]
assert base == "http://localhost:8080", "This validator is for local simulation only"
headers = {"Origin": base}
client = httpx.Client(base_url=base, auth=(values["DASHBOARD_USER"], values["DASHBOARD_TEST_PASSWORD"]), timeout=30)

def call(method, path, body=None):
    response = client.request(method, "/api/v1" + path, headers=headers, json=body)
    response.raise_for_status()
    return response.json()

assert httpx.get(base + "/api/v1/accounts", timeout=10).status_code == 401
account = next(item for item in call("GET", "/accounts")["items"] if item["mode"] == "mock")
assert account["mode"] == "mock"
assert client.patch(f"/api/v1/accounts/{account['id']}/pause", headers={"Origin": "https://invalid.example"}, json={"paused": False}).status_code == 403
call("PATCH", f"/accounts/{account['id']}/pause", {"paused": True})
source = call("POST", "/sources", {"account_id": account["id"], "kind": "profile_followers", "value": "validate." + uuid.uuid4().hex[:12], "label": "Stack validation", "requested_limit": 10})
job = call("POST", f"/sources/{source['id']}/collect", {"idempotency_key": str(uuid.uuid4())})
assert job["kind"] == "discover"
call("PATCH", f"/accounts/{account['id']}/pause", {"paused": False})
for _ in range(30):
    found = call("GET", f"/candidates?account_id={account['id']}&source_id={source['id']}&available=eligible")
    if found["total"]:
        break
    time.sleep(1)
else:
    raise AssertionError("Worker did not collect a sample")
call("PATCH", f"/accounts/{account['id']}/pause", {"paused": True})
target = found["items"][0]["target_id"]
rel = call("GET", f"/relationships?account_id={account['id']}&target_ids={target}")["items"][0]
first = call("POST", "/jobs", {"account_id": account["id"], "target_id": target, "kind": "follow", "idempotency_key": str(uuid.uuid4())})
second = call("POST", "/jobs", {"account_id": account["id"], "target_id": target, "kind": "follow", "idempotency_key": str(uuid.uuid4())})
assert first["id"] == second["id"]
call("PATCH", f"/relationships/{rel['id']}", {"expected_version": rel["version"], "protected": True})
subprocess.run(["docker", "compose", "--env-file", ".env.integration", "restart", "api", "worker"], cwd=root, check=True, capture_output=True)
for _ in range(30):
    try:
        call("GET", "/accounts")
        break
    except (httpx.HTTPError, OSError):
        time.sleep(1)
call("PATCH", f"/accounts/{account['id']}/pause", {"paused": False})
for _ in range(30):
    saved = call("GET", f"/jobs/{first['id']}")
    if saved["state"] != "queued":
        break
    time.sleep(1)
assert saved["state"] == "skipped" and saved["error"] == "protected"
rel = call("GET", f"/relationships?account_id={account['id']}&target_ids={target}")["items"][0]
assert rel["protected"] and not rel["ever_followed"] and rel["observed_state"] == "none"
call("PATCH", f"/accounts/{account['id']}/pause", {"paused": True})
assert client.get("/fonts/manrope-latin.woff2").status_code == 200
print("PASS: gateway authentication, Origin rejection, persisted discovery, deduplication, restart recovery, protection recheck, simulation isolation and production font asset")
