# Decisions and evidence

Prepared 2026-11-16. Technical docs and source reviewed; no Instagram login/actions performed.

## Decisions

| Decision | Reason |
|---|---|
| PostgreSQL rather than deployed SQLite | Multiple containers share durable operational state |
| DB-backed queue, no Redis | Lower footprint for initial one-account workload |
| Synchronous FastAPI SQLAlchemy + separate worker | Fits synchronous Instagrapi and keeps API responsive |
| One backend image | Shared policy/model code and simple updates |
| Next App Router + shadcn | User requirement, portable generated components |
| Caddy Basic Auth initially | Concrete single-owner authentication with small implementation surface |
| Simulation-only delivery | Honest project skeleton; live behavior needs session/recovery implementation |
| Separate content phase | Follow/unfollow ledger is user's first priority |
| Stable string IDs | Dedup survives username changes and avoids JS numeric precision loss |

## Version policy

Python 3.12, Node.js 24, PostgreSQL 16, Next.js 16/React 19, Tailwind 4 are chosen compatibility lines, not claims that every component is latest. npm lock records resolved frontend dependency versions. Python constraints records the tested environment. Instagrapi 3.2.15 was GitHub's latest release during inspection; pinning it does not prove every latest/master feature exists in that wheel. Confirm before live adapter work. Base container tags need digest pinning before production releases.

## Primary sources

- Instagrapi repository: https://github.com/subzeroid/instagrapi
- User operations: https://subzeroid.github.io/instagrapi/latest/usage-guide/user/
- Media: https://subzeroid.github.io/instagrapi/latest/usage-guide/media/
- Direct: https://subzeroid.github.io/instagrapi/latest/usage-guide/direct/
- Realtime: https://subzeroid.github.io/instagrapi/latest/usage-guide/realtime/
- Track/music: https://subzeroid.github.io/instagrapi/latest/usage-guide/track/
- Exceptions: https://subzeroid.github.io/instagrapi/latest/exceptions/
- Next installation: https://nextjs.org/docs/app/getting-started/installation
- shadcn Next setup: https://ui.shadcn.com/docs/installation/next
- FastAPI containers: https://fastapi.tiangolo.com/deployment/docker/
- Compose startup conditions: https://docs.docker.com/compose/how-tos/startup-order/

## Interpretation boundaries

Library method availability is distinct from tested account behavior. Requested follower ordering does not guarantee a chronological complete snapshot. One liker response is not exhaustive pagination. Music and scheduling support depend on format/account/availability. Platform restrictions can occur independently of client configuration. No external mutations were performed while creating this foundation.
