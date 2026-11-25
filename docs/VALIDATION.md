# Current feature validation — 2026-11-18

Authoritative feature state: IMPLEMENTATION-STATUS.md; checklist: FEATURE-TESTING-PLAN.md; owner guide: FEATURES.md.

- Installed schema0012, no Alembic drift, final production images recorded in STATUS; API/DB/web healthy, worker online.
- Full Python3.12/PostgreSQL suite190 passed, then final changed publication/relationship tests25 passed. Native123 passed/69 PG-only skipped. Ruff check/format61 files passed.
- Frontend TypeScript/production builds passed.44 desktop/mobile browser cases passed; later installed/publication smoke6 passed. Visual QA found mobile notification overlap; corrected stacking and regression verifies the approval is not covered. Post-fix affected/installed16 cases passed, screenshots visually reviewed under artifacts/screenshots.
- Both pinned-client validators passed with Docker networking disabled, including actual photo/story helper forms, one configure, no retry/recent-media inference, caption user tags preserved, and guarded removal.
- Actual Instagram: all seven bounded observation kinds; one three-account liker-watch baseline (2 HTTP requests); approved @jessiekamaraa follow(4)/unfollow(3) pair with original state restored and permanent history retained. Personal request usage31/94 today, archive27/158 unchanged by feature work.
- Both accounts paused, broad action flagsfalse, follow/unfollow allowances0, enabled watches0, active jobs/connections0. Live transport is scoped by per-workflow/intent checks, private .env.features; original .env.readonly unchanged.
- Latest backup20261118T224836Z-00759f79 restored into an isolated schema0012 database; session decryption3 revisions, content archive hashes0 assets and paused workflow/connection/media quarantine passed. Nonempty/missing/unsafe asset archive cases have unit proof.

**Unverified external gate:** actual owner photo/story publication, caption edit and story removal. No owner image supplied, content assets0, dispatched content mutations0. Only a reviewed saved draft may be used; synthetic fixtures must stay local. DM intake, video/carousels/music, scheduling, broader recurring follow/retention and public deployment are not accepted by this increment. Historical validation below describes earlier checkpoints.

---
# Delivery validation

## Browser onboarding release, 2026-11-18

Current scope and results are authoritative in IMPLEMENTATION-STATUS.md and UI-ONBOARDING-PLAN.md. Migration0008 and encrypted browser password/code handoff are installed. Python3.12/PostgreSQL136 tests passed; native89 passed/47 PG-only skipped. Browser desktop/mobile acceptance, installed real-only routes, circular loading, paused capabilities and visual checks are tracked in the status document. Production build, typecheck, offline pinned-client validation with zero networking, Alembic no-drift and isolated schema0008 backup restoration passed. The owner will perform actual Instagram login through Settings afterward; it has not succeeded yet. No new Instagram requests were sent for this implementation.

## Historical delivery records

The records below describe earlier checkpoints and are superseded by the browser release above, including their earlier claims that browser login is missing or Docker is unavailable.

Current evidence is recorded in IMPLEMENTATION-STATUS.md. As of2026-11-18:125 Python3.12/PostgreSQL tests pass through0007; native83 pass/42 PostgreSQL-only skip. Pinned-client checks with networking disabled verify default challenge stop, opt-in profile recovery and password-first MFA with one password call and one code submission. Owner checkpoints preserve budgets and stop on ownership loss. Cleanup assessments/permanent history have14 backend tests; generated contract, frontend typecheck and production build pass, but new cleanup browser acceptance is unrun. Earlier ten desktop/mobile scenarios, two later acquisition scenarios and restart validator passed. Backups restored into disposable databases with quarantine/device-context decryption without showing fields. Four owner-operated attempts used23 requests on Toronto2026-11-17 under an approved temporary ceiling30, now restored20. Last two stopped PROFILE_VERIFICATION_REQUIRED before a code prompt. No real authenticated login/read acceptance, follow/unfollow, public deployment or publication is verified. Credential-file loading and browser login are not implemented. The original delivery record below is historical.

Local TLS integration now also passes with TLS1.3 using the pinned Caddy image, a disposable in-memory internal CA and process-local certificate trust. Owner authentication, token replacement, Origin rejection, security headers and dashboard routing pass. The test proxy/temp files were removed and main services retained. This does not prove public certificate issuance or final host/domain configuration; see scripts/validate_tls.py and OPERATIONS.md.

Validated on 2026-11-16. This is a runnable simulation foundation plus implementation specifications, not a finished Instagram bot or Figma-generated dashboard.

## Completed

- 14 backend tests passed: authentication, Origin rejection, enqueue idempotency, key/intent conflict, simulation isolation, protection recheck, unmanaged cleanup rejection, permanent history exclusion, pause, version conflict, cancellation, future due time, timezone validation and unknown-field rejection.
- Initial Alembic migration applied to a fresh SQLite validation database. Seed ran twice with one account; second run was idempotent. Alembic check detected no schema/model drift.
- PostgreSQL offline migration SQL generated successfully. This establishes SQL generation, not live PostgreSQL execution or concurrency correctness.
- Frontend dependency installation, TypeScript check and Next.js 16.4.6 production build succeeded.
- Compose YAML parsed successfully; six services present (five long-running and one migration task).
- Shell setup/backup scripts pass shell syntax checking.
- Implemented FastAPI OpenAPI schema exported.

## Not executed here

- Docker Compose build/start, PostgreSQL runtime migrations, row locks, gateway Basic Auth, container network routing and TLS. Docker executable/daemon is unavailable in this environment.
- PowerShell setup on Windows (script provided; Windows runtime unavailable).
- Live Instagram login, discovery, action execution, music or publishing. Live adapter is intentionally unimplemented.
- Full dashboard visual/accessibility tests; only the minimal Next starter is supplied. Generate the dashboard from the standalone Figma Make brief.

## Dependency note

Tests reported one Starlette deprecation warning about its httpx TestClient integration. Tests pass; review upstream guidance when updating the test stack. Python constraints and npm package-lock capture resolved versions. Base image digests remain a deployment task.

## Product gaps tracked for Codex

Source discovery, live session onboarding, request budgets, recurring cleanup scheduler, job claim/lease recovery, unknown-result reconciliation, full response schemas, business deduplication across different request keys, richer API filters/settings and content pipeline are specified in component docs but not implemented. The simulator intentionally makes no Instagram requests and never changes live relationship state.
