# Codex handoff and ordered backlog

The approved IMPLEMENTATION-PLAN.md supersedes this original milestone order. Resume from IMPLEMENTATION-STATUS.md, which records tested code, external gates and exact next commands. The kickoff and backlog below are historical scaffold guidance, not the current completion state.

## Paste this into Codex after opening the extracted folder

> Read AGENTS.md, docs/00-START-HERE.md, docs/01-ARCHITECTURE.md and docs/VALIDATION.md. This is a Docker Compose project with Next.js/shadcn dashboard, FastAPI, PostgreSQL and a Python worker. The delivered worker is simulation-only. Begin with milestone 1 below: establish the local stack, validate PostgreSQL migrations and gateway auth, then close the core queue/ledger contract gaps. Keep the Figma UI behind a service provider while its backend endpoints are built. Use the documented Instagram adapter boundary and preserve the invariants. Do not label simulation as live or enable real Instagram actions as a side effect of setup. Implement the next milestone fully and report checks and remaining gaps.

## Milestone 1 — foundation hardening

- Run Compose with test .env; validate services, auth, Origin checks, DB migrations, seed and backups.
- Add response schemas, stable error envelope, account-level authorization checks, semantic source validation and server pagination.
- Make idempotency compare a full canonical payload; add permanent acquisition intent deduplication across different keys.
- Add settings, candidate/source collection contracts, audit/heartbeat read endpoints and associated migrations.
- Implement controlled mock discovery and request-count accounting fixtures.
- Acceptance: end-to-end simulated acquisition flow survives restart and protection updates; tests cover critical races on PostgreSQL.

## Milestone 2 — live adapter/session proof

- Inspect pinned Instagrapi 3.2.15 methods; bump/pin only if tested features require it.
- Implement secure session persistence, manual verification/recovery, credential redaction and capability checks.
- Implement bounded follower/liker collection, provenance, cursor checkpoints and completeness metadata.
- Add fresh relationship observation and initial import as unmanaged.
- Acceptance: read-only account verification and bounded collection work; no account restrictions are treated as empty results.

## Milestone 3 — durable execution

- Add committed claims, per-account lock, request budgets, attempt journal and unknown-outcome reconciliation.
- Implement follow/unfollow adapters, pre-dispatch policy checks, pending-request handling and observed result mapping.
- Add retention scheduler and operating windows; preserve protected/unmanaged history.
- Acceptance: crash/retry/rate-limit tests pass, then a controlled owner-authorized live loop is verified. P0 is not complete until this gate passes.

## Milestone 4 — Figma dashboard integration

Can proceed visually in parallel with earlier milestones, but live controls depend on completed API features. Import portable components into apps/web, replace mocks per screen, add query/form tooling, generate types from response schemas, implement mobile navigation and state/error views. Preserve runtime auth and same-origin requests.

Acceptance: Sources → Candidates → Queue → Ledger → Activity works from a phone and desktop. No live action has a success appearance until the server confirms it.

## Milestone 5 — deployment

Pin base image digests, build release images, deploy on lightweight host, configure HTTPS/tunnel, restore-test backups, monitor queue/worker status, secure session storage. Start paused. Document routine update and recovery commands with actual tested results.

## Milestone 6 — content pipeline

Implement docs/09 only after P0. Add content schema, sender allowlist, DM polling/watermarks, asset storage/editor, publishing/reconciliation and later insights. Add a bounded rendering worker only when required.

## Open decisions for implementation

Account handle and source list; configured budgets from owner's manual runs; exact server/domain; who should remain permanently protected; whether pending requests expire automatically; initial session onboarding interface; media retention. None prevent building the mock foundation. Avoid inventing budget guarantees, credentials or a live connected account.
