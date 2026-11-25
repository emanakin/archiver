# Frontend implementation requirements

## Stack and structure

Next.js App Router, TypeScript strict, React 19, Tailwind 4, shadcn/ui, Lucide. Starter uses a shadcn-style Button with Radix Slot, cva, clsx and tailwind-merge. Add remaining components through the shadcn CLI after importing Figma output. TanStack Query, React Hook Form and Zod are recommended additions when dashboard forms and server state arrive; they are not dependencies of the initial single-page app.

Target folders: `src/app/(dashboard)/...`, `src/components/layout`, `src/features/{sources,candidates,queue,ledger,activity,settings}`, `src/components/ui`, `src/lib/api.ts`, `src/lib/types`, `src/mocks`. Keep feature-specific mutations beside their feature. Server state stays in the query cache; global state should be limited to account selection and display preferences.

## Figma integration

Import visual components and styles; preserve App Router, Dockerfile and API boundary. Translate React Router links to Next Link and use client components only where needed. If the export references Figma-specific asset schemes, copy assets locally and replace imports. Never transplant a Vite build configuration into `apps/web`. The Make prototype can use fake local storage, but the integrated app must use PostgreSQL-backed API state.

Replace the mock provider one screen at a time. Do not show mock and live values together. Display account mode prominently. `live_metrics=null` means unavailable, not zero. Keep source collection and settings controls labeled unavailable until their backend endpoints exist.

## Data behavior

Use same-origin `/api/v1` calls through the proxy. No `NEXT_PUBLIC_` secrets. API error type includes status and detail; handle 401 (authentication), 403 (origin/access), 409 (conflict/state), 422 (input), 503 (temporarily unavailable). Future structured error codes are specified in FastAPI doc.

Polling target: queue 5–10 seconds while visible/running; overview 30 seconds; stop frequent polling when backgrounded. These poll intervals concern your own API, not Instagram. Debounce search and cancel stale requests. Add URL query params for tabs, filters, search and selected account; preserve these on refresh. Use server pagination before the ledger becomes large.

Protection toggles may optimistically display pending state but must roll back on failure. Send `expected_version`; on 409 refresh and explain. Never optimistically mark someone followed. Enqueue returns a job and shows Queued. Request status changes appear only after server observations. Buttons disable during submission to avoid duplicate clicks; reuse the same idempotency key across network retries for the same intent.

## Navigation and responsive behavior

See the standalone Make brief for all screens. Desktop sidebar; mobile bottom navigation and sheet-based details. Cards replace wide tables below 768px. Sticky action bars must reserve bottom-nav and safe-area height. Support 360/390/768/1280px widths and 200% zoom without losing controls. Date values are UTC on the wire, formatted with America/Toronto by default; label timezone for scheduling.

## Authentication and deployment

Caddy handles owner login. Do not invent a Next password form unless replacing the full auth scheme. With `npm run dev` alone the gateway route does not exist: either run the Compose stack and replace the web service command for development, or add a local Next rewrite to a local proxy with authentication still enforced. Do not embed the internal token in browser headers.

Starter Dockerfile produces Next standalone output, runs as a non-root user and serves on internal port 3000. Avoid runtime downloads of remote fonts; use system or committed local fonts. Keep server-only dependencies out of client components.

## Definition of done

All core screens from Make brief work against real API data; mock provider remains only behind explicit dev configuration. Keyboard/phone flows work; typecheck and production build pass. Unknown outcomes, partial collections and stale observations have distinct UI. Changing protection while a cleanup job is queued is visibly reflected. P0 API gaps in docs/11 must be completed before labeling the dashboard live-ready.
