# Codex working instructions

Read docs/00-START-HERE.md, docs/01-ARCHITECTURE.md, docs/11-CODEX-HANDOFF.md and docs/VALIDATION.md before implementation.

## Resuming implementation

Read docs/IMPLEMENTATION-PLAN.md and docs/IMPLEMENTATION-STATUS.md first. The approved implementation plan supersedes the older handoff milestone order. Continue the first incomplete increment using current files as truth. Update status after each meaningful increment with files/behavior changed, exact checks/results, outstanding gates and next action. Check off roadmap acceptance only after verification; keep partial code and externally gated live checks visibly incomplete. Never require chat history to resume.

## Product priorities

P0: durable follow/unfollow ledger, protected accounts, source discovery, serialized execution and clear operating status. P1: DM content inbox, draft editing, music and publishing. Dashboard: mobile-first Next.js App Router + TypeScript + shadcn/ui, generated separately with Figma Make.

## Invariants

- Stable Instagram IDs are strings everywhere. Usernames are mutable labels.
- No repeat follow after a confirmed or requested past follow. Existing follows and pending requests are skipped. Keep history after unfollow.
- Simulation must never update real observed relationships, follow timestamps, counters or `ever_followed`.
- Protect/exclude checks occur again immediately before external mutation. Only explicitly managed relationships qualify for cleanup.
- A timeout after a write has unknown outcome. Reconcile it before considering another write.
- Serialize all Instagram requests per connected account. Do not assume one follow job equals one HTTP request.
- Challenges/restrictions pause affected execution and preserve session data. Never retry those in a loop.
- Do not add credentials, cookies, private messages or session material to source control, browser bundles, logs or fixtures.
- API validates all inputs; client-side checks are UX only. Never trust an account ID supplied by a future multi-user caller without ownership authorization.

## Implementation rules

Use migrations; never call create_all on production startup. Keep Instagram access behind a backend adapter. Queue jobs from API handlers; do not call Instagram inside HTTP requests. Keep external calls outside DB transactions. The existing simulation transaction intentionally contains no external I/O and is not a template for a live call.

The delivered code is a scaffold with gaps enumerated in the docs. Do not label mock metrics as live. Do not silently activate live mode as part of UI integration. Before implementation, verify the installed Instagrapi signatures against the pinned version; docs/latest can move.

Preserve generated frontend components where practical. Remove mock persistence and bridge its service layer to the documented API. Update OpenAPI, docs and meaningful tests when changing contracts. Run backend tests, frontend typecheck/build and PostgreSQL integration checks relevant to the change. Report unrun checks honestly.

No deployment, publication or Instagram actions are implied by merely opening this repository. Implement and validate locally until the owner connects an account and explicitly enables the intended workflow.
