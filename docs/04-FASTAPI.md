# FastAPI technical specification

## Runtime requirements

Python 3.12; FastAPI; Pydantic v2/pydantic-settings; SQLAlchemy 2 synchronous sessions; psycopg 3; Alembic; Uvicorn. Backend and worker use one image and different commands. Optional `instagram` extra pins Instagrapi 3.2.15 for the initial implementation investigation. Core scaffold dependencies have a generated constraints file; update versions deliberately with tests. No CPU-heavy video or Instagram work in API handlers.

## Implemented endpoints

All application routes use `/api/v1`; health paths are internal. JSON IDs are strings. API requires `X-Internal-Token`; proxy supplies it. Mutations also require exact `Origin == APP_ORIGIN` (CLI callers must supply this header). JSON schemas reject unknown input fields.

| Method / path | Behavior |
|---|---|
| GET /accounts | List provisioned archive records |
| PATCH /accounts/{id}/pause | `{paused: boolean}`; append audit |
| GET /overview?account_id=... | Account, job counts, mode, `live_metrics:null` |
| GET /sources?account_id=... | Sources |
| POST /sources | account_id, kind, value; duplicate => 409 |
| GET /relationships?account_id=...&limit=50&offset=0 | Joined ledger/user labels; limit 1–100 |
| PATCH /relationships/{id} | expected_version, protected/excluded; stale => 409 |
| GET /jobs?account_id=... | Most recent 100 jobs |
| POST /jobs | Durable follow/unfollow/reconcile intent, 202 |
| POST /jobs/{id}/cancel | Queued only; otherwise 409 |
| GET /actions?account_id=... | Most recent 100 actions |
| GET /openapi.json | Protected schema |
| GET /health/live, /health/ready | Process/DB checks; direct internal paths |

Current list wrappers are `{items:[...]}`; relationship list includes limit/offset. The exported `contracts/openapi.json` is generated from implemented handlers. Response types are presently dictionaries; add explicit Pydantic response models before client code generation.

Example queued simulation job:

```json
{"account_id":"<uuid>","target_id":"demo-0","kind":"follow","idempotency_key":"follow-demo-0-request-1","scheduled_at":"2026-11-18T15:00:00Z"}
```

Queue time requires a timezone. Idempotency uniqueness is scoped to archive account. Reusing a key for another target/kind returns 409. If simultaneous duplicate writes race, current scaffold returns 409; the next milestone should fetch and return the existing job after comparing a canonical request fingerprint, including schedule/payload.

## Planned P0 endpoints

- POST /accounts/{id}/connect and /recover: initiate server-controlled session setup; sensitive input never logged. Design the credential transport before implementing the form.
- PATCH /sources/{id}; POST /sources/{id}/collect: queue a bounded discovery job; return 202/job ID.
- GET /candidates: cursor pagination, source/state filters and eligibility reasons.
- POST /jobs/bulk: requested candidate IDs, deterministic per-target idempotency; response separates accepted/existing/rejected.
- GET/PATCH /accounts/{id}/settings: budgets, operating window, timezone, retention/pending policy, version.
- POST /relationships/{id}/adopt: explicit enrollment of existing relationship; record timestamp and owner action.
- POST /jobs/{id}/reconcile: check uncertain write; prohibit blind retry.
- GET /audit-events and /worker-status: owner actions, heartbeat, stale-worker signal.

Do not present these endpoints as implemented. Align their response types with the Figma provider through a frontend adapter.

## Validation and errors

Normalize @handles, validate allowed characters and max lengths, canonicalize post URLs to Instagram media identifiers and remove tracking params. SourceCreate currently validates type/length only: add semantic validation before live discovery. Reject unsupported hosts/schemes and limit body sizes. For uploaded files in phase 2, validate decoded content and enforce size limits.

Future error envelope: `{error:{code,message,retryable,request_id,details}}`. Stable codes: SOURCE_INVALID, RELATIONSHIP_PROTECTED, ALREADY_TOUCHED, VERSION_CONFLICT, ACCOUNT_PAUSED, BUDGET_EXHAUSTED, OUTCOME_UNKNOWN, SESSION_REQUIRED. Redact library payloads and secret-bearing URLs. Do not return stack traces or raw responses to the browser.

## Transactions and permissions

Queue intent and audit atomically; use a unique business constraint as well as HTTP idempotency. PATCH relationship locks its row and increments version. Worker repeats eligibility before dispatch. Add per-account advisory locking and lock-order discipline in live phase. A protection update that commits before final dispatch check must block cleanup; if dispatch already started, explain that it cannot be recalled.

The owner has access to all accounts in this single-user app. For future multiple owners, require ownership joins on every object lookup; random UUIDs alone do not authorize access. Keep DB/API ports internal even in development Compose.

## Observability

Add request IDs, structured logs, latency, status and route template. Avoid recording Authorization headers, internal tokens, Instagram IDs in high-cardinality metric labels, session JSON or private media URLs. Log job/attempt IDs to correlate API and worker. Current scaffold logs worker job ID/state only. Add readiness checks for schema version and worker heartbeat before production.
