# Figma Make brief — Archive Desk MVP

Build a polished, interactive mobile-first web dashboard named **Archive Desk**. This document is self-contained. The app manages one owner's Instagram archive, beginning with source discovery, a permanent follow/unfollow ledger, protected accounts and a scheduled action queue. The owner also models and curates fashion, film/pop culture, running and technology. The visual identity should feel like a thoughtful independent editorial archive.

## Deliverable and stack

Target Next.js App Router + React + strict TypeScript, Tailwind CSS 4 and shadcn/ui. Use Lucide icons. Use reusable React components and a typed service abstraction. If this environment can only generate a Vite/React app, produce portable components and a clearly separated router, then state that adaptation is needed; do not claim it is a Next.js export.

Create a working prototype with local mock data, simulated latency and state changes. All mock metrics and simulated actions must be explicitly labeled. Do not connect to Instagram or build the backend. All list actions, filters, forms, drawers and navigation must work in the prototype. Do not replace the requested application with a landing page.

## Visual direction

Dark editorial interface, matte surfaces, generous spacing, restrained emerald accents. Background #101112, cards #191B1E, borders #303338, main text #F4F4F5, muted text #A1A1AA, primary #8AE6B1. Amber denotes attention, red denotes errors. Use a clean sans-serif; tabular numerals for counts and timestamps. Radius 12–16px. Avoid gradients, neon glows, heavy glass effects and decorative fake charts.

Use compact typography with a clear hierarchy: 24–30px screen titles on mobile, 14–16px body text. Touch targets at least 44px. At 390px wide, the UI must feel intentionally composed. No page-level horizontal scrolling. On desktop, use a 224px sidebar and a bounded content area. On mobile, use a sticky bottom nav plus safe-area padding; drawers become bottom sheets. Use shadcn Button, Card, Badge, Sheet, Dialog, AlertDialog, Tabs, Select, Switch, Input, DropdownMenu, Tooltip, Skeleton and responsive tables/cards.

## Navigation

Desktop sidebar: Overview, Sources, Candidates, Queue, Ledger, Activity, Settings. Below a divider, show Content as a disabled “Phase 2” destination. Mobile bottom navigation: Home, Sources, Queue, Ledger, More. More opens Candidates, Activity and Settings. Use path-equivalent routes `/`, `/sources`, `/candidates`, `/queue`, `/ledger`, `/activity`, `/settings`. Every detail view can use a Sheet and a query parameter or local state.

Global header: archive account avatar/handle, account-state badge, current mode badge and Pause control. States: Paused, Running, Cooling down, Needs attention, Disconnected. For multiple accounts only show a future placeholder; do not build multi-tenant onboarding. Prototype default: @archive_demo, Simulation mode, Paused. Never imply a real Instagram connection.

## Screen 1 — Overview

Show a greeting-free title “Overview”, account status and latest synchronization time. Four operational cards: candidates available, queued follows, unfollows due, protected accounts. A second region shows configured follow/unfollow budgets with used/remaining values and “Configured allowance” labeling; these are operator settings, not platform limits.

A compact 7-day chart may show sample recorded follows and follow-backs, with a visible “Sample data” label. Do not infer causation or show made-up live revenue. Put “Run summary” below: processed, skipped, failed, needs review. Include next scheduled job and recent activity. If no account data exists, provide a setup checklist that links to Sources and Settings.

Pause is immediate in the prototype: current in-flight operation may finish, no additional job starts. Resume after a restriction requires the user to acknowledge recovery status in a dialog. Ordinary resume does not need repeated confirmation.

## Screen 2 — Sources

Tabs: Profiles, Posts. Add source opens a mobile sheet / desktop dialog. Profile input accepts @handle; post input accepts an Instagram post/Reel URL. Fields: friendly label, enabled switch, candidate collection size, requested ordering for profile followers (“Default”, “Newest requested”, “Oldest requested”). Explain briefly: “Instagram may not return a complete or chronological list.”

Each card shows source label, type, handle/URL, last collection time, candidates discovered, overlap count and last result. Actions: Collect candidates, View candidates, Disable. Collection moves through queued/running/completed/partial/error states. Use a bounded sample collection that adds 3–5 fixture candidates. Show partial results as partial; never claim a follower list was exhausted unless the response confirms it.

## Screen 3 — Candidates

Search handle; filter by source, eligibility and relationship. Desktop table; mobile stacked cards. Fields: avatar initials, username, source badges, discovered time, state badge, reason for exclusion, protected indicator. Fixture categories: eligible, previously followed, already following, requested, protected, excluded and unknown.

Eligibility must be visible. The user can select eligible rows and choose Queue follows. Disable selection for ineligible rows and explain the reason. Bulk bar sticks above mobile navigation. Queueing returns job entries; it does not immediately change the relationship to Following. Include a preview count and the distinct excluded/skipped counts.

Detail sheet: user identity, stable ID, source history, observed relationship, last checked, flags, action timeline. Actions: Protect, Exclude, Queue follow if eligible, Recheck state. A protected person cannot be automatically followed or unfollowed in this product. “Exclude” skips acquisition and cleanup; “Protect” also expresses a relationship the user wants preserved.

## Screen 4 — Queue

Tabs: Scheduled, Running, Completed, Needs review. Filter follow/unfollow/reconcile and date. Display target, operation, scheduled time, source if known, attempt count, mode and status. Use states queued, running, succeeded, simulated, skipped, failed, unknown, cancelled, blocked. Simulated must have its own neutral badge, not a success badge implying a real follow.

Allow cancellation only while queued. Failed read jobs may show Retry. Unknown write outcome shows “Check outcome”; do not provide blind “Retry follow”. Show pause reason and recovery action for blocked jobs. Do not claim cancellation can undo an operation already sent to Instagram. Offer a detailed activity drawer for each job.

## Screen 5 — Ledger

Tabs: All, Following, Requests, Previously followed, Protected. Search by username and stable ID. Show observed state, managed/unmanaged, follow/request dates, cleanup due date, last checked, protected switch and source provenance.

A previously followed target remains in history after unfollow and cannot be requeued for acquisition. Existing relationships imported at connection time are unmanaged and excluded from automatic cleanup. Include “Adopt into management” as a planned action in the mock, with explicit confirmation explaining the future cleanup effect. No bulk “unfollow everyone” control.

Protected toggle updates the row, counters and related queued unfollow status in the simulation. Display version-conflict handling: “This record changed. Refresh to apply your update.” Avoid silent last-write-wins behavior. Use an example where a queued unfollow is skipped after protection changes.

## Screen 6 — Activity

Chronological audit feed with filters for action, account/configuration changes and errors. Each record shows time, target, operation, result and useful reason. The detail drawer can show job ID, request identifier and timestamps, but never tokens, passwords, cookies or raw private-message bodies. Distinguish simulated action history from live history. Error copy should explain the user's next step in plain language.

## Screen 7 — Settings

Sections: Account status; acquisition controls; cleanup; schedules; protected/excluded management; timezone. Fields: configured follows/day, unfollows/day, request ceiling, collection batch size, allowed operating window, retention days (default 15), pending-request expiry policy (default manual review), timezone (America/Toronto).

Use explicit save/cancel for forms. Label budgets as owner-configured, with no claim of a universally safe Instagram limit. A restriction pauses execution. Include mock session states Connected, Expired and Needs verification, with “Open recovery instructions”; do not collect Instagram passwords or implement a fake successful login.

Scheduler uses the selected timezone for windows and local display. Payloads use UTC ISO timestamps. Provide a non-working future content settings area as a labeled roadmap card, not a fake operational toggle.

## Phase 2 preview

Keep Content disabled in this MVP. Design may include one preview card describing “Send a post by DM, edit it, then queue it.” Do not spend implementation effort on carousel editing, music browsing, publishing or calendar screens yet. The first usable dashboard must prioritize growth operations.

## Typed data contract

Use string IDs, including Instagram IDs. Never parse them as JavaScript numbers. Put the following types in a shared types module and use mock service methods rather than embedding data directly into JSX:

```ts
type Mode = 'mock' | 'live';
type RelationshipState = 'unknown' | 'none' | 'following' | 'requested';
type JobState = 'queued' | 'running' | 'succeeded' | 'simulated' | 'skipped' | 'failed' | 'unknown' | 'cancelled' | 'blocked';
interface ArchiveAccount { id: string; username: string; mode: Mode; paused: boolean; }
interface Candidate { target_id: string; username: string; source_ids: string[]; first_seen_at: string; eligible: boolean; reason: string | null; }
interface Relationship { id: string; account_id: string; target_id: string; username: string; observed_state: RelationshipState; managed: boolean; protected: boolean; excluded: boolean; ever_followed: boolean; version: number; follow_requested_at: string | null; follow_confirmed_at: string | null; unfollow_due_at: string | null; last_checked_at: string | null; }
interface Job { id: string; account_id: string; target_id: string | null; kind: 'follow'|'unfollow'|'reconcile'|'discover'; state: JobState; scheduled_at: string; error: string | null; }
```

Service layer: getOverview, listSources, createSource, collectCandidates, listCandidates, enqueueFollows, listRelationships, patchRelationship, listJobs, cancelJob, listActivity, getSettings, updateSettings, setPaused. Return promises and simulate a brief loading interval. Keep provider swap easy: `MockProvider` now, `HttpProvider` later.

Implemented backend endpoints available later: GET accounts/overview/sources/relationships/jobs/actions; POST sources/jobs; PATCH relationships/{id}, accounts/{id}/pause; POST jobs/{id}/cancel under `/api/v1`. Candidate collection, settings, advanced filters and bulk actions are planned contracts. Implement their mock behavior without claiming the backend already exists. Add a small adapter layer where the prototype shapes differ from backend shapes.

## Fixture scenarios and behavior

Create at least 16 synthetic candidates, 6 synthetic sources, 12 jobs and 20 activity entries. Use fictional handles, initials and placeholder art; do not use real profile pictures or pretend live data was fetched. Include mixed eligibility, two protected accounts, an existing unmanaged follow, a pending request and an unknown write outcome.

Test these interactive paths:
1. Add a profile source, collect a sample batch, filter candidates and queue eligible follows.
2. Pause/resume, then simulate completion; show simulated jobs and leave observed relationships unchanged.
3. Protect a managed account before scheduled cleanup; its job is skipped.
4. Encounter a simulated restriction; queue pauses with clear recovery state.
5. Enter an invalid URL; show inline validation without losing the form.
6. Empty, loading, error and stale-data states are available through a development-only fixture switch.

## Accessibility and handoff

Keyboard access, visible focus, labeled fields, semantic headings and accessible dialog titles. Do not communicate status by color alone. Charts need a text summary. Dragging is not required for this phase. Respect reduced-motion preferences. Keep mobile forms above the keyboard and avoid hover-only actions.

Output modular files: app shell, account header, metric cards, source form/list, candidate cards/table, job cards/table, ledger detail, activity feed, settings form, reusable state components, types and service provider. Include a short README stating any framework adaptation required. Preserve the private-operations nature of the app: no pricing, marketing hero, fake billing or public signup.
