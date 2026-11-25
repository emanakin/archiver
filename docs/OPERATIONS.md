# Release, backup and recovery

The local feature release is recorded in IMPLEMENTATION-STATUS.md. Browser connection, bounded reads, one liker-watch baseline and one explicitly approved follow/unfollow pair have real acceptance. Photo/story publication still needs an owner-selected saved draft. Public hosting remains an owner-operated gate; no server has been deployed. The current local stack uses private .env.features (live transport), all accounts paused and broad action workflows off.

## Update the local release

### Private Cloudflare R2 bucket

Create a private R2 bucket and an **Object Read & Write** token scoped to that bucket. Keep public access disabled. Set `CONTENT_R2_BUCKET` and `CONTENT_R2_ENDPOINT` in the local `.env.features` file. Use the HTTPS endpoint shown by Cloudflare. Save the token's access key ID and secret access key in the ignored `secrets/content_r2.json` file under `access_key_id` and `secret_access_key`. Cloudflare R2 provides an S3-compatible API with region `auto`. [R2 authentication](https://developers.cloudflare.com/r2/api/tokens/)

Start the workspace from `archive-desk` with the R2 overlay, then run the storage probe.

```powershell
docker compose --env-file .env.features -f compose.yaml -f compose.instagram.yaml -f compose.r2.yaml up -d --wait
Get-Content scripts/validate_content_r2.py -Raw | docker compose --env-file .env.features -f compose.yaml -f compose.instagram.yaml -f compose.r2.yaml exec -T api python -
```

The probe uploads a temporary fixture, checks immutable reuse and verified decoding, checks unsigned access, and removes the fixture. R2 originals are saved under account and SHA-256 keys. `CONTENT_STORAGE_BACKEND=r2` in `.env.features` keeps imports and uploads held until the R2 configuration is ready. A DM check can still capture the share and caption so the image import can be retried later. [R2 conditional operations](https://developers.cloudflare.com/r2/api/s3/conditional-operations/)

Set `DM_POLL_INTERVAL_SECONDS` in the selected Compose environment to configure automatic DM polling. The default is 900 seconds, and the installed feature workspace uses 60 seconds. Recreate the API and worker after changing the value. Polls still respect the account's direct-publishing grant, pause, cooldown, operating window and daily request allowance.

Set `DM_SELF_RECEIPTS=true` for the account selected by `DM_DEFAULT_ACCOUNT_ID` to send receipts to its verified Instagram self conversation. Draft and error links use `APP_ORIGIN`. A receipt with uncertain delivery stays in Queue for review.

Set `DM_SPOTIFY_REFRESH_MINUTES` to control the worker's Spotify metadata refresh. The default is 240 minutes. The latest attempt and successful sync are durable. A failed refresh keeps the prior snapshot available for music selection.

For local captions, add `-f compose.captions.yaml` to the feature Compose command. Start `ollama`, then use `docker compose ... exec -T ollama ollama pull MODEL` for `hf.co/ggml-org/SmolVLM-500M-Instruct-GGUF:Q8_0` and `qwen2.5:0.5b-instruct`. The container runs on the private Compose network. Set `DM_CAPTION_VISION_MODEL` to the SmolVLM tag, `DM_CAPTION_MODEL` to the Qwen tag and `DM_CAPTION_SOURCES` to comma-separated reference handles, with the primary voice first. A completed recent-post observation in Insights refreshes the available examples for that profile. Set `DM_DEFAULT_ACCOUNT_ID` to the account's Archive Desk ID and `DM_DEFAULT_FROM` to an offset-aware activation time. Recreate API and worker.

New shares wait one DM polling interval plus 15 seconds for overrides. If the next inbox check or caption cannot be verified, the default publication stays in Drafts. Clear `DM_DEFAULT_ACCOUNT_ID` to disable the defaults for future shares. Disable direct publishing in Content to revoke unused publication approvals.

The legacy `compose.bucket.yaml` remains for recovery of prior local-bucket originals. This workspace had zero assets at cutover. Older keys are retired under the ignored `secrets/retired-local-bucket/` folder. Restore those keys to their original paths only for an isolated legacy recovery. Keep the current R2 and Instagram session keys private. Generated release and test logs live under ignored `artifacts/validation/`.

`scripts/backup.py --include-content` exports and checks originals from both backends into `content-assets.tar`. To restore original images after validating the database and backup manifest, point a stopped isolated workspace at the restored database and its configured bucket. Copy the archive to that API container and run `python -m app.content_backup restore /path/in/container/content-assets.tar`. Restore writes missing objects and checks existing bytes. Keep a private backup of object store credentials, separate from the image archive.

From `archive-desk`, select the private environment file explicitly. `.env.integration` is for local simulation tests. Use `.env.features` for the live workspace. Include `compose.r2.yaml` after installing the credentials file. Fresh installations default to read_only until a workflow is explicitly approved.

```powershell
./services/backend/.venv/Scripts/python scripts/backup.py --env-file .env.features --include-sessions --include-content
docker compose --env-file .env.features -f compose.yaml -f compose.instagram.yaml -f compose.r2.yaml stop worker
docker compose --env-file .env.features -f compose.yaml -f compose.instagram.yaml -f compose.r2.yaml build
docker compose --env-file .env.features -f compose.yaml -f compose.instagram.yaml -f compose.r2.yaml run --rm migrate
docker compose --env-file .env.features -f compose.yaml -f compose.instagram.yaml -f compose.r2.yaml up -d
docker compose --env-file .env.features -f compose.yaml -f compose.instagram.yaml -f compose.r2.yaml exec -T api alembic check
docker compose --env-file .env.features -f compose.yaml -f compose.instagram.yaml -f compose.r2.yaml ps
```

For an Instagram installation add `-f compose.yaml -f compose.instagram.yaml` to Compose build/start/exec commands, after packaging the pinned sibling with `scripts/package_instagram.py`. Never run independent migrations concurrently. Keep account queues paused during an update; confirm readiness reports the expected schema, worker heartbeat is fresh and attempts show no unresolved writes before resuming an enabled workflow.

The checked-in Dockerfiles and Compose use tested base digests for Python 3.12, Node 24, PostgreSQL 16 and Caddy 2. A base upgrade changes those digests deliberately and reruns the container/backend/browser checks. Instagrapi is pinned separately by version, Git commit and source archive checksum; the default Compose target installs it. Backend runtime runs as UID 10001; web runs as UID 1001.

## Backup and prove restoration

```powershell
./services/backend/.venv/Scripts/python scripts/backup.py --env-file .env --include-sessions
./services/backend/.venv/Scripts/python scripts/restore_drill.py backups/CREATED_DIRECTORY --env-file .env
```

The backup creates a custom-format PostgreSQL dump, optional encrypted session archive and a checksum manifest under ignored `backups/`. It uses container files plus Compose copy, preserving binary data on Windows. Immutable session revisions let a post-dump ciphertext snapshot include every referenced session. Avoid migrating while backing up. Copy completed backups to private encrypted off-host storage; a file on the same disk is insufficient for disk loss. Choose retention to match storage (initial planning target: seven daily and four weekly).

Back up `secrets/instagram_session_key` separately. It is never in a session archive or dump. On Linux keep the directory 0700, files 0600, and a worker-readable key; Windows owners should restrict the source directory ACL. The opt-in Compose file mounts ciphertext and the separate read-only key only in the worker. Never mount either in web/API.

The drill verifies hashes, restores into a generated disposable database, checks schema and account counts, and puts that restored copy into quarantine: all accounts paused, workflow flags disabled, read-only proof cleared, queued jobs held for review, dispatched unfinished jobs unknown. Active browser handoffs are cancelled, password/code envelopes erased and published onboarding keys removed before any restored worker starts. It also pauses liker watches, invalidates pending paired tests and holds queued/uncertain publication drafts. It verifies every active/pending session pointer exists in the archive and rejects unsafe archive paths without extracting them. It then drops only its generated database. It never replaces the running database.

To additionally prove session decryption, supply `--session-key PATH_TO_SEPARATE_KEY`. No session fields are displayed. An archive with zero referenced sessions proves only empty-session handling; it does not prove real account recovery. The native backend dev environment supplies cryptography for this optional check.

New originals use the private bucket when configured. Existing local originals live in the shared content-assets volume mounted only in API and worker. Include `--include-content` once drafting is available. The restore drill verifies referenced UUID.original files against restored database SHA-256 hashes and rejects unsafe, duplicate or link members. Restore the matching originals before preview or publication. The empty workspace backup passed its restore drill, and a separate bucket fixture passed a nonempty backup and restore check.

## Recover an actual installation

Restore into a separate stopped environment first. Apply the quarantine SQL used in `scripts/restore_drill.py` to that restored database before starting a worker. Restore matching ciphertext and the separate key; use the same pinned image and apply any forward migrations. Keep acquisition/cleanup disabled while verifying the session privately with the commands in INSTAGRAM-SETUP.md. Restriction recovery must also be reviewed in Instagram's official app.

Review the gap between backup time and the last known action. A stale backup can lose permanent follow history for actions performed after its snapshot. A fresh friendship read cannot reconstruct a past follow that has since ended. Acquisition must stay disabled until missing history is recovered from a newer ledger/backup and uncertain work is reconciled; do not claim that restoring yesterday's dump preserves an unrecorded follow forever. No automatic restoration path resumes blocked or unknown writes.

For each dispatched uncertain relationship job, use its queue detail to request read-only reconciliation. For media jobs use Content and the actual publication receipt/owner-reviewed media link; never guess from recent posts or reconfigure an upload. Failed reads alone may be retried. Never requeue a follow/unfollow by editing its state. Audit sources, protection/exclusion flags, managed adoption and retention before enabling cleanup. Restore targets (RPO <=24h, RTO <=1h for a small DB) are planning goals, not measured production results.

## Host deployment gate

For a local HTTPS check of the pinned Caddy image and shared gateway configuration:

```powershell
./services/backend/.venv/Scripts/python scripts/validate_tls.py --env-file .env.readonly
```

The validator starts a uniquely named disposable proxy on a free loopback port, uses an internal CA held in container memory, and trusts its public certificate only inside that validation process. It checks certificate/hostname verification, modern TLS negotiation, missing/wrong/correct owner authentication, replacement of a forged internal token, Origin rejection, security headers and dashboard routing. It removes its proxy and temporary files; the main services and host trust store are unchanged. It requires the private local test environment's DASHBOARD_TEST_PASSWORD without printing it. Tested here with TLS1.3. This proves local HTTPS behavior, not public DNS/certificate issuance or the final host firewall/HTTPS APP_ORIGIN configuration.

Deployment needs the chosen Linux host/domain and owner authorization. Start paused with workflow flags disabled. The default stack binds only `127.0.0.1`; an SSH tunnel preserves this setup. For public HTTPS follow 08-INFRA.md: set the exact HTTPS APP_ORIGIN and domain SITE_ADDRESS, expose 443 through the firewall, and validate certificate issuance and gateway authentication before access. Only Caddy gets host ports. API, DB, web and session files remain private. Local HTTP testing does not prove public TLS.

Dashboard operating status exposes heartbeat, oldest due work, request usage, last collection/relationship freshness, restrictions and uncertain writes. Readiness checks DB connectivity and the exact migration head; worker health is separate so a stalled worker cannot silently look healthy just because API reads work. Logs rotate per container and library exception bodies are suppressed. The owner should verify backup age/off-host copy and available disk space as part of routine operations.
