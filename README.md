# Archive Desk

Archive Desk is an Instagram archive dashboard with a DM-driven intake, review and publication workflow.

## Start here

1. Read [Architecture](docs/01-ARCHITECTURE.md) and [Operations](docs/OPERATIONS.md) for the runtime boundaries and setup.
2. The extracted design is already integrated in `apps/web`; no additional Figma generation is needed.
3. On Windows with Docker Desktop running: `powershell -ExecutionPolicy Bypass -File scripts/setup.ps1`.
4. With Python 3.12 and `cryptography` installed, run `python scripts/init_session_key.py` once. Then run `docker compose up --build -d`.
5. Open http://localhost:8080 and use username `owner` with the dashboard password entered during setup.
6. Add/connect Instagram in the browser, then enter a verification code when requested. No seed or terminal login is needed. The existing local installation uses `.env.readonly`; select that environment explicitly when managing it.

## What is actually included

- Seven-screen Next.js dashboard with persistent sources, candidates, queue, ledger, activity and versioned settings. Mobile and desktop layouts use self-hosted fonts.
- FastAPI typed responses, OpenAPI-generated frontend types, source normalization, server pagination/filters, canonical request receipts and business deduplication.
- PostgreSQL migrations, source provenance, worker health and encrypted browser-to-worker onboarding.
- Caddy owner authentication by default, with an optional Google sign-in overlay for isolated workspaces.
- Durable account locks, committed claims, attempt journal, request/action budgets and unknown-outcome reconciliation.
- Opt-in guarded Instagram connection, read-only collection, reviewed follows and managed cleanup preserving follow-backs.
- Opt-in daily source collection, operating status, pinned base images and checksum-verified backup/restore tools.
- Backend, PostgreSQL concurrency, browser and local stack validators.

The application defaults to `read_only`. Password and verification-code forms are available in the owner dashboard. Open [Infrastructure](docs/08-INFRA.md) for HTTPS configuration and deployment guidance.

## Local prerequisites

Docker Desktop with Linux containers and Compose v2 on Windows. Linux server: Docker Engine + Compose plugin. Optional native development: Python 3.12 and Node.js 24. No Instagram credentials are required to run the scaffold.

## Documents

| File | Audience |
|---|---|
| 01-ARCHITECTURE | Runtime, boundaries, data flow and decisions |
| 02-FIGMA-MAKE-BRIEF | Figma Make: self-contained dashboard specification |
| 03-FRONTEND | Codex: integrate generated UI into Next.js |
| 04-FASTAPI | Backend contracts, auth, validation and implementation requirements |
| 05-DATABASE | Ledger semantics, schema, constraints and migrations |
| 06-WORKER | Jobs, serialization, budgets, failures and recovery |
| 07-INSTAGRAM | Instagrapi mapping and adapter contract |
| 08-INFRA | Compose, Windows, lightweight server, backups and operations |
| 10-TESTING | Verification matrix and release acceptance |
| 11-CODEX-HANDOFF | Ordered implementation backlog and pasteable kickoff |
| 12-DECISIONS-AND-SOURCES | Decisions, assumptions, versions and source links |
| VALIDATION | Checks performed on this delivery and remaining checks |

## Developer commands

```powershell
cd services/backend
py -3.12 -m venv .venv
.\.venv\Scripts\python -m pip install -c constraints.txt ".[dev]"
$env:INTERNAL_API_TOKEN = "local-test-token-at-least-24-chars"
.\.venv\Scripts\python -m pytest
```

```powershell
cd apps/web
npm ci
npm run typecheck
npm run build
```

Use the Compose gateway for integrated development. Running Next alone does not route `/api` to FastAPI; see the frontend doc. Do not expose Next/API directly on the server.

## Integrated verification

Use the normal setup for an owner-chosen password. During development, the ignored `.env.integration` contains generated local test credentials and must never be committed or printed.

```powershell
docker compose --env-file .env.integration -f compose.yaml -f compose.test.yaml run --build --rm tests
cd apps/web
npm run typecheck
npm run test:e2e -- --reporter=line
```

The active browser suite includes intercepted synthetic onboarding scenarios and GET-only checks of the installed real workspace. It never initiates an Instagram login. PostgreSQL tests use fresh, isolated schemas; native tests skip those cases unless TEST_POSTGRES_URL is configured. `scripts/validate_stack.py` and `tests/legacy` are historical simulation checks and must not be run against the real stack.
