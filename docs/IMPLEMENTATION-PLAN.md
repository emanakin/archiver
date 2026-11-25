# Archive Desk implementation roadmap

Approved 2026-11-16. This is the implementation contract; progress and commands belong in IMPLEMENTATION-STATUS.md. Checkboxes mean acceptance is verified, not merely that code exists.

The completed browser readiness scope is tracked in **UI-ONBOARDING-PLAN.md**. Current feature increments and actual read/pair evidence are tracked in **FEATURE-TESTING-PLAN.md** and **IMPLEMENTATION-STATUS.md**; these supersede CLI-first onboarding and historical “no real account/action” notes below. One approved pair has passed; broader recurring acquisition/retention, actual media publishing and deployment gates remain separate and incomplete.

## Product decisions and architecture

Build a persistent dashboard, simulated acquisition, reliable execution, read-only Instagram, approved follows, automatic cleanup, deployment, then content. Keep Next.js App Router/React/Tailwind, FastAPI, SQLAlchemy/Alembic, PostgreSQL queue, a separate worker and Caddy owner authentication. Port the sibling `.donor-ui` components; do not transplant Vite. Package the sibling `instagrapi` checkout at `c0383cc7abb75aebfccbe4dca27ace2c184e489f` as a reproducible backend wheel.

One owner/account initially. Candidate follows require review. Initial account connection/recovery uses a backend command. Cleanup keeps follow-backs. Collection is manual initially; recurring collection is opt-in later. Approved DMs create drafts requiring publication approval. Default retention is 15 elapsed days; timezone America/Toronto; pending requests require manual review. Owner supplies live budgets before activation. Setup does not authorize Instagram writes or deployment.

## Milestones

### M1 — Stack and imported dashboard
- [x] Validate Docker Linux engine, Compose, PostgreSQL migrations, seed twice, gateway authentication and Origin checks using Python 3.12/Node 24.
- [x] Port seven screens, mobile/desktop navigation, dialogs, styles and self-hosted fonts into Next App Router; serve public assets in production.
- [x] Introduce HTTP service and TanStack Query; retain fixtures only in explicit development configuration.
- [x] Connect existing account/pause/source/ledger/protection/exclusion/job/cancel/action endpoints; label unavailable features and remove integrated prototype tools/sample metrics.
- [x] Acceptance: operations persist across reloads/restarts; all screens work on desktop/mobile without fictional backend state.

### M2 — Complete simulated acquisition
- [x] Explicit response models, stable errors/request IDs, account validation, pagination/filters, normalized sources and semantic Instagram URL validation.
- [x] Source labels/order/limits/status, bounded queued mock discovery, cursor/checkpoint/partial metadata and overlap provenance.
- [x] Candidate eligibility/filtering/recheck and bulk enqueue accepted/existing/rejected results.
- [x] Versioned settings, audit reads, worker status and server-derived overview.
- [x] Canonical request idempotency and follow-intent deduplication across different keys.
- [x] Wire all seven screens and save/cancel/conflict flows to PostgreSQL-backed API state.
- [x] Acceptance: source → collection → review → queue → simulation → activity survives restart; protection invalidates jobs; simulation never changes observed relationships, follow history or real counters.

### M3 — Reliable execution framework
- [x] Committed claims/leases, append-only attempts, dispatch markers, external calls outside transactions and advisory account lock on a dedicated connection.
- [x] Ownership loss stops dispatch; expired dispatched writes reconcile instead of requeueing.
- [x] Atomic action/request budgets/reservations, actual transport counting, bounded operations, operating windows and local-calendar resets.
- [x] Separate manual pause/session/restriction/cooldown/budget states; heartbeat every 15 seconds, stale after 60 seconds.
- [x] UI attempt/deferred/blocked/unknown/reconciliation states; retry only eligible reads.
- [x] Acceptance with fake transport: duplicate workers, timeouts, termination and DB failures cannot silently repeat a mutation.

### M4 — Read-only Instagram

Checked transport/session items have offline or synthetic proof. Real account acceptance below remains mandatory.
- [x] Build the inspected, commit-pinned sibling library into backend; verify installed signatures/dependency compatibility.
- [x] Application-owned narrow client wrapper disables transport retries, timeout replay and automatic challenge replay; every request passes budget/account guards.
- [x] Connection/validation/recovery commands prompt for secrets (never argv/logs), use account lock/request accounting and persist encrypted session on worker-only volume with separate key.
- [ ] Resolve users, fresh relationship reads, bounded followers/likers/checkpoints/provenance and explicit inaccessible/partial/completeness metadata.
- [ ] Import existing follows/requests as unmanaged and already touched. UI shows real connection status/validation/recovery instructions; writes disabled. Following import/encountered requested targets are implemented; complete outgoing-request inventory is still unimplemented/unverified (the pinned request-list helper is incoming).
- [ ] Acceptance: connected account survives restart; authorized bounded read works; restrictions never appear as empty success.

### M5 — Reviewed live follows

Checked policy and ledger behavior has fake-transport proof. No real follow has been enabled or performed.
- [x] Explicit per-account acquisition enablement independent of session/pause; positive owner budgets and read-only proof required.
- [x] Fresh relationship and final flags/history/budget/account checks before dispatch; swallowed library precheck failures cannot reach a write.
- [x] Confirm follows versus requests by observation; mark system-created connections managed; both permanently prohibit repeat acquisition.
- [ ] Unknown results reconcile before any repeat; distinct UI queued/confirmed/requested/skipped/unknown states.
- [ ] Acceptance: owner-authorized small live run with source → target → job → attempt → observation trace and crash/timeout proof.

### M6 — Cleanup preserving follow-backs

Checked scheduling and policy behavior has fake-transport proof. Real cleanup acceptance remains open.
- [x] Deterministic retention jobs from confirmed follows (15 elapsed days default), no duplicate intents.
- [x] Fresh follow-back/relationship read immediately before unfollow. Keep reciprocal/protected/excluded/unmanaged relationships; defer unknown reciprocity.
- [x] Pending outgoing requests stay manual. Explicit audited adoption starts retention at adoption when historical follow date is unknown.
- [ ] UI cleanup reasons and permanent history after unfollow.
- [ ] Acceptance: eligible nonreciprocal managed cleanup works; late protection/new follow-back blocks dispatch.

### M7 — Recurrence and deployment
- [x] Per-source schedules disabled by default, same lane/checkpoints/budgets/windows; follow approval remains manual.
- [x] Deterministic restart/DST scheduling; operational queue age/heartbeat/freshness/requests/restrictions/unknown metrics.
- [ ] Validate release images/HTTPS/private networking/session permissions, backup restoration and DB/session recovery. Pin image digests.
- [ ] Deploy paused only after host/domain and deployment authorization; enable workflows individually and document tested operations.

### M8 — Content workflow
- [ ] M8A: stable-ID sender allowlist, bounded DM pagination/watermarks/deduplication, supported shares/URLs to drafts, recoverable inaccessible submissions and Inbox UI.
- [ ] M8B: immutable originals, revisioned captions/order/crop/attribution/permissions/schedules, authenticated bounded storage and mobile editor with save/cancel/conflicts.
- [ ] M8C: approval of specific revision/slot, serialized publication attempts/reconciliation, photo/carousel first, separately validated video/music and manual finalization for unsupported combinations; local scheduling only initially.
- [ ] Acceptance: duplicate DMs create one draft, edits survive restart, changed revisions require renewed approval, uncertain uploads never blindly republish; insights are actual observations with freshness.

## Contracts and verification

`/api/v1` remains authoritative. Add response schemas before TypeScript generation; explicitly map donor source type/value to backend kind/value. Add collection/candidate/bulk/settings/recheck/adoption/reconciliation/audit/worker interfaces and explicit mode/capability/session/freshness/unavailable-metric fields. All IDs are strings. Incremental Alembic migrations; no production create_all. Poll active work every 5 seconds, overview every 30 while visible; invalidate relevant queries on mutation. Same-origin browser requests; no browser secrets.

Every milestone updates OpenAPI/docs and records actual checks. Verify PostgreSQL migrations/concurrency/idempotency/version conflicts/restarts; fake-transport protection/budget/ownership/crash/unknown/DST cases; zero network/ledger contamination in simulation; frontend typecheck/build and desktop/390px/keyboard/deep-link/error/stale flows. Read-only proof precedes follows; controlled follows precede cleanup; acquisition reliability precedes publishing. Never mark live/deployment gates complete from local unit tests.
