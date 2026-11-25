# Instagram adapter requirements

## Choice and evidence

Instagrapi is an unofficial Python client. The repository/latest docs inspected on 2026-11-16 exposed the methods below. GitHub reported release 3.2.15; master/latest documentation may contain changes beyond that release. The optional extra pins 3.2.15 as the initial candidate, not a claim of live validation. At implementation time inspect installed signatures and test the actual pinned package. Store adapter version with job diagnostics.

## Capability mapping

| Product operation | Candidate method |
|---|---|
| Resolve source | user_id_from_username / user_info_by_username |
| Followers | user_followers / user_followers_v1_chunk |
| Requested newest ordering | order='date_followed_latest' |
| Streaming | iter_user_followers_v1 |
| Following snapshot | user_following(use_cache=False) |
| Source posts | user_medias / user_medias_paginated |
| Post likers | media_likers |
| Relationship observation | user_friendship_v1 / user_friendships_v1 |
| Follow / unfollow | user_follow / user_unfollow |
| DM inbox/thread | direct_threads / direct_messages |
| Media identity/info | media_pk_from_url / media_info |
| Downloads | photo_download / album_download / clip_download |
| Publication | photo_upload / album_upload / clip_upload |
| Feed music | photo_upload_with_music / album_upload_with_music |
| Music search | search_music / music_search_v2 |
| Feed scheduling | schedule_at on supported helpers |
| Own insights | insights_account / insights_media |

## Adapter interface

Expose domain methods, not raw library objects: resolve_user; collect_followers(cursor,amount,order); collect_likers; get_relationship; follow; unfollow; read_thread; get_media; download_asset; publish. Each result includes observation time, completeness, cursor where applicable and diagnostic request IDs. Normalize IDs as strings, booleans explicitly and inaccessible/unknown separately from empty data.

A public source URL does not guarantee visibility to the authenticated archive. Never circumvent private account access. Newest ordering is requested, not guaranteed. `media_likers()` implementation inspected returns one server result; do not promise an exhaustive liker list. Followers are mutable while paginating: persist observations and deduplicate across pages/runs by stable ID.

## Follow/unfollow semantics

`user_follow()` true can mean following OR outgoing request; false can mean already connected. Observe status to classify it. The inspected helper catches exceptions from its precheck and may proceed to the write; therefore your wrapper must perform its own preflight and propagate restrictions before invoking the helper. Do not treat library-internal checks as your final policy enforcement.

For unfollow, inspect both following and outgoing_request. A false following flag alone doesn't establish a pending request was cancelled. Keep request cancellation as a separate product policy even if the endpoint is shared. Verify actual relationship after an uncertain response. Preserve ever_followed after either accepted follow or request.

## Sessions

Use a long-lived client per account, serialized calls, saved settings and stable session context. Read password/2FA only through a dedicated backend recovery operation. Persist session settings as a secret: encrypted at rest with a separately backed-up key or restrictive server secret storage, accessible only by the worker. Never store plaintext session JSON in generic jobs, audit logs or the frontend. Log only session status and last validation time. No credentials are included in this project.

Documented settings helpers include load_settings/dump_settings; do not assume importing a browser cookie establishes a valid mobile session. Include a manual verification path and stop repeated login attempts. The app must remain useful when the account is disconnected.

## Request accounting and limitations

Wrap transport to measure actual requests, not just method calls. Library fallbacks and pagination may multiply calls. Configure bounded timeouts. Treat rate/challenge feedback as stop signals; a library does not remove platform restrictions. Availability, media downloads, music eligibility and account-specific capabilities need a live test matrix.

## Alternate implementation

Keep a Playwright adapter only if a particular UI operation is needed. Do not automatically switch to another transport to continue a restricted operation. Both adapters must share the same ledger, account locks, budgets and unknown-outcome handling. Official publishing can be added as another adapter for operations it supports.

Sources: https://github.com/subzeroid/instagrapi ; https://subzeroid.github.io/instagrapi/latest/usage-guide/user/ ; https://subzeroid.github.io/instagrapi/latest/usage-guide/media/ ; https://subzeroid.github.io/instagrapi/latest/usage-guide/direct/
