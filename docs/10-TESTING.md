# Test plan and acceptance

## Scaffold checks

Run `python -m pytest` from services/backend with dev dependencies. Tests exercise duplicate enqueue, same-key/different-intent conflict, protected/unmanaged skips, simulation isolation, pause, stale version, auth/origin enforcement and cancellation. SQLite supports these functional tests but does not establish PostgreSQL locking behavior. Build/typecheck the Next starter separately.

Generate OpenAPI from `scripts/export_openapi.py` using a development internal token. Schema is for implemented endpoints only. Validate Compose via `docker compose config --quiet`, migrate an empty PostgreSQL DB, run seed twice and check idempotence. End-to-end smoke: Basic Auth 401 without credentials, successful authenticated page/API, mutation rejected with wrong Origin, simulation completes without modifying relationship flags.

## Live milestone tests

| Scenario | Required result |
|---|---|
| Duplicate target from two sources | One target/relationship, both provenance links |
| Repeated HTTP submission | Same job; different request under same key rejected |
| Different keys for same follow intent | Business constraint prevents duplicate acquisition |
| Previously followed then unfollowed | Never reacquired automatically |
| Pending private request | Preserved separately; no second follow request |
| Protected after queueing | Skipped at dispatch |
| Existing unmanaged follow | Never auto-cleaned |
| Two workers | One request lane per account |
| Kill before external dispatch | Reclaimable with no duplicate write |
| Kill after external success | Unknown then reconcile, never blind repeat |
| DB outage during write | Preserve/recover ambiguous attempt |
| Throttling/challenge | Account pauses, queue retained, no retry storm |
| Partial follower list | No false claim of unfollow or completeness |
| Stale cache | Fresh pre-mutation observation |
| Daily quota race / timezone boundary | Atomic limits and correct day |
| DST operating window | Deterministic one-time scheduling |
| Simulation mode | Zero Instagram transport calls and no live ledger mutations |

## UI acceptance

390px/desktop layouts, keyboard operation, clear mode and data freshness, no secrets in bundle/network responses, field errors preserve input, 409 version conflicts refresh cleanly, queueing never looks like confirmed follow, unknown outcome offers reconciliation. Test localStorage mock behavior is disabled in integrated mode.

## Content phase tests

Repeated DM delivery, more messages than one poll page, mixed share schemas, inaccessible post, oversized/misleading file, redirect to private IP, carousel reorder, music failure, timed-out publish, draft changed after scheduling, worker crash mid-upload and permission/source record preservation.

## Release gates

No claim of production readiness until PostgreSQL integration, Compose gateway and actual chosen-account capability tests pass. Keep successful local checks distinct from live Instagram validation. Deploy initially paused, then enable an explicitly configured small run after the owner connects the account. Record version and results; do not infer safe action limits from a passing unit test.
