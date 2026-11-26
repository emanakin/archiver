# Infrastructure and operations

## Services and sizing

Long-running containers: db, api, worker, web, proxy. migrate is a one-shot initialization/update task. Start with 2 vCPU / 4 GB RAM / 30+ GB SSD as a planning allowance, not a benchmark. The configured memory ceilings total approximately 1.9 GB, leaving room for OS/cache and deployment overhead on 4 GB. A 2 GB host may run a small prebuilt deployment but has little margin. Build Next images elsewhere if memory is tight. Video processing is out of scope for this sizing.

No Redis, Kubernetes or browser container in the initial runtime. PostgreSQL persists in a named volume. Phase 2 adds a bind-mounted media directory or object storage and possibly a separate render worker. Avoid MinIO on the initial small host unless its API is actually required.

## Windows local setup

Install Docker Desktop with WSL2/Linux containers. Extract the ZIP to a path such as `C:\Projects\archive-desk`, open PowerShell there, and run `powershell -ExecutionPolicy Bypass -File scripts/setup.ps1`. The script generates random DB/token secrets and asks you to generate a Caddy bcrypt hash interactively. It does not overwrite an existing .env.

Run `docker compose config --quiet`, `docker compose up --build -d`, then `docker compose exec api python -m app.seed`. Open http://localhost:8080. All host ports bind 127.0.0.1 by default. The database and Python API have no host ports. Logs: `docker compose logs --tail=100 api worker`. Stop without deleting data: `docker compose down`.

## Linux server deployment

Use a supported Linux distribution, Docker Engine and Compose v2. Clone/copy source and create `.env` with `sh scripts/setup.sh`. For remote access, prefer SSH port forwarding while prototyping: `ssh -L 8080:127.0.0.1:8080 user@server`. Browse localhost:8080 through the tunnel. This preserves the default gateway Origin.

Public HTTPS requires a domain resolving to the server. Set `SITE_ADDRESS=archive.example.com`, `APP_ORIGIN=https://archive.example.com`, `BIND_ADDRESS=0.0.0.0`, `HOST_HTTPS_PORT=443`. Caddy uses TLS-ALPN validation on 443; ensure firewall and provider allow it. Automatic HTTP redirects are disabled in the supplied config, so use HTTPS explicitly. The 8080 mapping is unused by the domain-mode Caddy listener; remove it in a production override if desired. No wildcard DNS certificate/DNS provider is configured.

Never expose the local HTTP Basic Auth endpoint publicly without TLS. Keep API/DB host ports closed. Protect the .env file and backup material with owner-only permissions. On a hardened server move secrets from .env to a secret manager/file mounts as the session design matures.

## Builds and dependency discipline

Compose waits for healthy DB, then completed migration, then healthy API/web. Restart policies apply to runtime services. Base images now use tested digests; npm/Python dependency locks are checked in. Instagrapi packaging verifies its reviewed sibling Git commit and archive checksum. See OPERATIONS.md for current update/recovery commands.

Build commands: `docker compose build`; migration: `docker compose run --rm migrate`; start: `docker compose up -d`. After a schema change, snapshot DB, stop the worker, apply migration, start API and worker, then verify. Do not run multiple independent migration jobs concurrently. A dependency condition is not a distributed migration lock.

## Backups and restoration

Use `scripts/backup.py` on Windows or Linux for a binary-safe custom dump, checksum manifest and optional encrypted session archive. `scripts/restore_drill.py` restores only into a generated disposable DB, verifies referenced session revisions and exercises paused recovery quarantine. See OPERATIONS.md. Keep the session key separately and copy backups to private encrypted off-host storage. The older backup.sh remains a minimal Linux dump-only alternative.

Restore drill into a new database, never over production casually:
1. Stop API/worker writes to the target restore environment.
2. Create an empty `archive_restore` DB using `createdb -U archive archive_restore` inside db.
3. Copy a dump into the container (`docker compose cp backups/<file>.dump db:/tmp/restore.dump`).
4. Run `docker compose exec db pg_restore -U archive -d archive_restore --no-owner /tmp/restore.dump`.
5. Point an isolated API/worker configuration at the restored DB, keep account paused, run integrity checks and reconcile external state before resuming.

Restored data can predate Instagram actions. Never resume from backup without checking uncertain/recent relationships. Recovery goals to validate: daily backup RPO <=24h, restore RTO <=1h for a small DB; these are targets, not measured claims.

## Operational checks

Watch disk space, backup age, due queue age, worker heartbeat, blocked accounts and unknown outcomes. Readiness checks DB connectivity plus the expected migration head. Dashboard operating status separately displays worker heartbeat, freshness, requests and uncertain writes. Log rotation is configured per container. Future media work must be bounded so it cannot starve acquisition.

## Verification limits

See VALIDATION.md. Docker daemon availability is required for Compose runtime checks. Local unit tests cannot validate PostgreSQL row-lock semantics, container DNS, gateway authentication or TLS issuance.

## Google workspace rollout

The Google overlay uses the existing public HTTPS origin. Staging uses a separate
PostgreSQL database and synthetic Instagram accounts. Keep the public gateway on
its existing configuration until the owner and a second Google account have
verified sign-in and workspace isolation.

In Google Auth Platform, create a **Web application** client. Its authorized
redirect is `https://myarchiver.net/auth/google/callback` for production.
Use a separate client and exact callback for the staging hostname. Store the
downloaded web client JSON at `secrets/google_oauth.json`. The container process
runs as UID 10001 and needs read access to that file. Keep the file outside Git
and restrict host access. Configure the Google audience for the two test users.

After taking a database backup and building the new release, run this from the
deployment checkout using the site's existing Compose overlays. The role script
writes private database URLs to its output file and preserves the owner's
existing default playlist.

```sh
docker compose --env-file .env.production -f compose.yaml run --rm migrate
docker compose --env-file .env.production -f compose.yaml run --rm --no-deps -T \
  --user 0 -v "$PWD/secrets:/output" migrate python - /output/google-db.env \
  < scripts/provision_workspace_roles.py
```

Add `ARCHIVE_OWNER_EMAIL` and the approved `ARCHIVE_SIGNUP_TEST_EMAILS` to
`secrets/google-db.env`. Leave `ARCHIVE_SIGNUP_OPEN=false`. Pass that file as a
second `--env-file` and append `-f compose.google.yaml` after the production
overlay. Recreate `api`, `worker`, `web`, and `proxy` together. Confirm that
the owner sees both existing Instagram accounts and the pilot sees an empty
workspace. Check that the pilot cannot retrieve the owner's known draft preview
or job URL. Once both checks pass and Google is ready for the production
audience, set `ARCHIVE_SIGNUP_OPEN=true` and recreate the API.

Keep the Google gateway in place once a second workspace exists. To suspend
admission during an incident, set `ARCHIVE_SIGNUP_OPEN=false`, clear the pilot
allowlist, and recreate the API. Existing workspaces can still sign in.
