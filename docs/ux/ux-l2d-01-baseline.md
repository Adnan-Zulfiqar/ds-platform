# DP-Phase-2 — UX-L2D-01 Baseline + UX Audit

Phase 2 — UX-L2D / Dashboard Design (renamed from UX-L2C on 2026-09-15; see note). Milestone UX-L2D-01 (baseline and audit; no product changes). Author: Claude. Date: 2026-09-15.

Evidence (owner-held, outside Git): `DP-Phase-2-evidence\ux-l2c-01-baseline\` — 58 screenshots (desktop 1440×900, tablet 1024×768, mobile 390×844; light and dark) plus `capture-report.json` (per-page overflow/console probes, products-row-click probe, tab-order probe). Rendered from a standalone build of the exact Phase 1 tree on `127.0.0.1:3138` with **every API call answered by a local mock** (no backend, database, Redis, broker, provider, or production access). The 80 CI screenshots of the editor from Phase 1's final run (`DP-Phase-1-evidence\ci\artifact\test-results\`) were also used.

Phase 1 closure: the Phase 1 ledger in the repository (`docs/agent-tasks/phase-1-progress.md`) sits at the accepted SHA `c0092da6` and was **not** amended — any commit to `fix/ci-baseline-security-pytest` would replace the SHA Cursor accepted before it is merged. The owner's acceptance (Cursor verdict `PASS — PHASE 1 MAY CLOSE`, 2026-09-15) is recorded here and should be carried into the repository docs by the first Phase 2 documentation commit (§J, UX-L2D-01 closing commit) or by the PR #7 merge commit message.

> **Naming note (owner decision, 2026-09-15).** This programme was started as "UX-L2C" and renamed **UX-L2D** because `origin/feature/ux-l2c-live-state-clarity` (`ece8322`) already carries substantial, unmerged, not independently accepted UX-L2C work that must keep its historical identity. Everywhere below, UX-L2D-0x is the new dashboard-design programme; "UX-L2C" without a milestone number refers to that historical branch. The audit itself is unchanged from the delivered UX-L2C-01 report.
>
> **State at the time of writing.** Phase 1 (`c0092da6`) is independently accepted (Cursor: `PASS — PHASE 1 MAY CLOSE`); PR #7 is **not** merged. UX-L2B is present in `develop` (`a543b029`). Historical UX-L2C exists remotely, unmerged, not independently accepted, gated by UX-L2D-GATE-04. The Celery exclusive-pidbox multi-worker/restart drill remains a separate safe-staging validation item and is not part of UX-L2D.

---

## A. Repository Baseline

| Item | Value |
|---|---|
| Repository | `Adnan-Zulfiqar/ds-platform` (`origin` = https://github.com/Adnan-Zulfiqar/ds-platform.git) |
| Phase 1 accepted SHA | `c0092da6dde6f5dfa586436efd63a33097ed7e7c` — exists locally and on `origin/fix/ci-baseline-security-pytest`; tree `99ed4f48` |
| PR #7 | **OPEN, draft, not merged** (`mergedAt: null`); base `develop` |
| `origin/develop` | `a543b02914064582c906d59e5619f214f94c9994` — Phase 1 is **not** in `develop` |
| `origin/main` | `3ce66d488e94ad3805fe24903deda99691c234a6` — Phase 1 not in `main`; `main` is 206 commits behind `develop` and has 9 commits `develop` lacks (divergence; not touched) |
| Phase 2 branch (created, local only, unpushed) | `feature/ux-l2d-dashboard` at `c0092da6` in worktree `C:\dspuxl2cdash`; `git status` clean (0 entries); no commit made |
| Phase 2 base decision | **Stacked on the accepted Phase 1 SHA**, i.e. `develop` + the 20 Phase 1 commits. Reason: `develop` alone has red CI (the Celery/RabbitMQ, PG17, cookie-origin and Sign-in repairs live only in Phase 1), and stacking touches neither `develop` nor the accepted SHA. When PR #7 merges — fast-forward is possible because `develop` has not moved — the Phase 2 branch rebases trivially; a squash-merge would require one rebase onto the new `develop` before Phase 2's own PR. I did not merge PR #7; no authorization to merge exists. |
| Main workspace | `C:\Users\profe\Documents\DS Platform` on `feature/ux-l1-professional-polish` (`f33b27b`), one untracked directory `scripts/ebay-oauth-t1/` — not touched |
| Owned resources | Build output `C:\dspuxl2cdash\frontend\.next-r7` (git-ignored; **retained** for UX-L2D-02 visual verification, ~disposable), synthetic `frontend/.env.local` (git-ignored, API origin `127.0.0.1:8138`), standalone server on `:3138` (started and **stopped**; port free). Ports 3000/8000/8001 not used. |

**Documentation drift found (record, do not fix in this milestone):**

1. `develop` **is** the tip of `feature/ux-l2b-publish-integrity` (both `a543b029`), i.e. UX-L2B R5–R7 were fast-forwarded into `develop`, yet `PROJECT_ROADMAP.md` and `CHANGELOG.md` on `develop` still say UX-L2B is "not merged" and "UX-L2C not started".
2. **A different "UX-L2C" already exists.** `origin/feature/ux-l2c-live-state-clarity` (`ece8322`, 10 commits, owner-authored 2026-09-10…12, based on `develop`, worktree `C:\dspuxl2c`, clean, unmerged, "not independently accepted") is titled *"UX-L2C — live-state clarity and calm completion journey"* (R1–R3). It changes 39 files (+6298/−1141): adds the missing `/products/[productId]` page, `lib/product-lifecycle.ts` lifecycle labels, `shopify-listing-status.tsx`, editor-header/publish/review-panel changes, a vitest setup and unit tests, two E2E suites, and `scripts/r2_/r3_provision_stack.py`. It overlaps directly with UX-L2D-04 (Drafts), -05 (editor) and -06 (integrations) and fixes finding F-1 below. **This needs an owner decision before UX-L2D-04 starts** (§L). It does not block UX-L2D-02/-03, which touch different files.

## B. Frontend Architecture

| Aspect | Fact (from `frontend/package.json` and source at `c0092da6`) |
|---|---|
| Framework | Next.js **15.1.6** (App Router, `output: "standalone"`), React **19.0.0**, TypeScript ^5.7.3, Node 24 locally |
| Styling | Tailwind CSS ^3.4.17 + `tailwindcss-animate`; HSL CSS-variable tokens in `app/globals.css` (`:root` + `.dark`): background/foreground, card, popover, primary (blue 221 83% 53%), secondary, muted, accent, destructive, **success**, **warning**, border/input/ring, `--radius: 0.5rem`; font Inter via `next/font` (self-hosted) |
| Components | shadcn-style primitives on Radix (`components/ui/`: alert, avatar, badge, button, card, coming-soon, dialog, dropdown-menu, empty-state, error-state, form, input, label, page-header, separator, sheet, skeleton, table, textarea, tooltip). **Missing primitives:** select (13 native `<select>` in app/components), tabs (editor has bespoke tabs), checkbox, switch, toast, popover, progress, breadcrumb, pagination, command palette |
| Icons | `lucide-react` ^0.469.0 |
| Charts | `recharts` ^2.15.4 (deprecated line; used only by 3 dashboard charts) |
| Rich text | TipTap ^3.30 (editor description) |
| Data | TanStack React Query v5 — every server read/write goes through `services/*.ts` (`auth, automation, dashboard, drafts, global-rules, integrations, inventory, notifications, orders, pricing, products, publish-readiness, stores, users`), each owning endpoint + query key + types (`types/api.ts`) |
| UI state | Zustand v5 `stores/ui-store.ts` — only `sidebarCollapsed` (persisted as `droppilot-ui`) and `mobileSidebarOpen` |
| Auth | Access token in memory only; httpOnly refresh cookie; `AuthProvider` + `AuthGuard`; `middleware.ts` cheap redirects; theme via `next-themes` (`attribute="class"`, default `system`) |
| Shell | `layouts/app-shell.tsx`: fixed-height flex, desktop sidebar (`w-64`, collapsible to `w-16`, hidden `< md`), `TopNav` (h-16: mobile menu, current page label, disabled Search, Notifications menu, disabled Help, ThemeToggle, UserMenu), `main#main-content` scrolls internally; skip link + `:focus-visible` ring + reduced-motion in `globals.css` |
| Navigation model | `lib/navigation.ts` (data, single source): 6 sections / 14 items — Main: Dashboard · Product Management: Drafts (badge), Products (badge), Import History, Inventory, Pricing, Suppliers *(coming-soon)* · Sales: Orders, Shipments, Customers *(coming-soon)* · Stores: Connected Stores · Analytics: Analytics · System: Automation, Notifications, Settings. Badges from `GET /products/workspace-counts` |
| Routes (App Router) | Public: `/` (redirect stub), `/login`, `/register`, `/forgot-password`, `/terms`, `/privacy`, `/unauthorized`, `not-found`. Protected `(protected)`: `/dashboard`, `/drafts`, `/drafts/[productId]`, `/products`, `/imports/history`, `/inventory`, `/pricing`, `/orders`, `/orders/[orderId]`, `/shipments`, `/customers` (ComingSoon), `/stores`, `/analytics`, `/automation`, `/notifications`, `/settings`, `/settings/integrations`, `/settings/global-rules`. **No `/products/[productId]`** (see F-1). No `/suppliers`, `/settings/{profile,team,billing}` (declared unavailable) |
| Client/server split | 94 files carry `"use client"`; 5 of 18 protected pages are client pages (dashboard, analytics, shipments, …); list pages are server pages wrapping client tables — reasonable |
| Backend surface relevant to the dashboard (verified in `backend/app/api/v1/*/router.py`) | `GET /analytics/dashboard` (one aggregate payload; no currency field), `GET /products/workspace-counts` `{drafts, products}`, `GET /drafts` and `GET /products` (shared `page/size≤500/sort_by/sort_dir/q`; sortable `created_at, updated_at, title, status, cost_price_min…`; searchable `title, external_id, supplier_name`), `GET /products/imports` (+ `POST …/retry`), `GET /orders/statistics`, `GET /notifications` + `/unread-count`, `GET /integrations/{aliexpress,shopify,ebay}/status`, `GET /stores`, `/stores/statistics`, `/stores/{id}/health`, `GET /drafts/{id}/listings`, `POST /integrations/shopify/publish-readiness` (per draft), `GET /drafts/{id}/seo-score`, `POST /products/{id}/optimize` (AI = `StubProvider` until a real provider is configured). **No endpoint returns "ready to publish" counts, per-draft readiness in bulk, or a lifecycle/status filter** |
| Tests | 32 Playwright spec files (`tests/e2e/`), two projects (`chromium`, `mobile-chrome`; CI runs chromium only); Phase 1 provider-isolation fixture; **no unit/component test runner** on this tree (vitest exists only on the other UX-L2C branch); no axe/a11y tooling |

## C. Screen Inventory

Legend — Priority: P1 must fix in Phase 2 · P2 should · P3 nice. "Current state" is what the mocked render or code shows.

| Screen | Purpose | Primary action | Current state | Problems observed | Priority |
|---|---|---|---|---|---|
| `/login`, `/register`, `/forgot-password` | Entry | Sign in / create workspace | Card layout, Google button, legal footer; recently polished (UX-L1, Phase 1) | Out of Phase 2 scope; consistent. No change proposed | P3 |
| `/dashboard` (Home) | Orientation | *none* — page has no action | "Welcome back" + 8 order-sync stat cards + 6 KPI cards + 3 charts + optional recent-activity list. Hard-coded period 30d, **currency hard-coded `"USD"`** (`dashboard/page.tsx:54`, tenant `defaultCurrency` is available on identity), chart axes show `$` | Answers none of the merchant questions (attention, drafts, ready, failed, next action). 14 metric cards above the fold; empty workspace = 14 zeros with no guidance (`desktop-light-dashboard-EMPTY.png`). Duplicates `/analytics` minus the period selector. Nothing links to Drafts | **P1** |
| `/drafts` | Core workspace: review imported products | Open a draft / Import | Table with 9 columns; row click → editor; 3 buttons + overflow menu per row; skeleton + empty state present | **No page gutter** (title flush to sidebar, button flush to right edge) — `space-y-6` without `p-4 sm:p-6` unlike other pages; **no thumbnails** (icon placeholder; list `Product` type has no image URL — backend dependency); titles truncated to ~14 chars; redundant `draft` badge on every row; raw enum status text; **no search, sort, filter, pagination** (`useDrafts({ size: 25 })`, `meta.hasNext` ignored) although the API supports all four; no readiness/blocker signal; no bulk selection; on mobile the actions column is off-screen (horizontal table scroll); "Optimize with AI"/"History" compete visually with "Edit Draft" | **P1** |
| `/drafts/[productId]` (editor) | Edit + publish | Save (autosave) / Review & publish | Strong: header with identity/status/actions, 11 tabs in 4 groups, right-rail "Before you publish" checklist, mobile sheet + sticky bar, full M2A conflict flow (banner → reload-confirm dialog / review dialog). CI screenshots prove desktop/tablet/mobile/dark | **Content area has no gutter** (inputs flush to sidebar border and right edge at every width — `main` horizontal overflow flagged on all editor captures); tab strip overflows at 1024 and below with no affordance; `ai-studio` and `history` tabs are dashed placeholders ("Stage 6"); after a successful publish the header still says "Draft · Draft saved — not live" (`ux-l2b-visual-evidence/14-successful-publish.png`); "View in DropPilot Products" → 404 (F-1); two readiness vocabularies (client `readinessFor` score/level vs server blockers/recommendations); native `<select>` for store | **P1** (shell/gutters/tabs), P2 (vocabulary) |
| `/products` | Published catalogue | Open a product | Same table component as Drafts (`variant="products"`), rows not clickable, title/image link to `/products/{id}` | **Every product link 404s** — probe landed on `/products/aaaa…0006` → "Page not found" (`desktop-light-products-row-click-result.png`); 2 console 404s per visit from link prefetch. No listing/store/last-sync columns although `StoreListing` data exists per product | **P1 (Critical)** |
| `/imports/history` | Import job log | Retry failed | Table of import records with retry | No gutter; not linked from Drafts (where import failures matter); no filter | P2 |
| `/settings/integrations` | Connect supplier + channels | Connect / Reconnect / Retry webhooks | Good: explicit text badges ("Reconnection required", "1 needs webhook setup", "Connected", "Webhooks last confirmed"), inline errors, privacy note, coming-soon providers greyed | Top-nav label shows "Settings" (no breadcrumb); page is the only place to *fix* a store yet Home never points to it; AliExpress expiry not surfaced anywhere else | P2 |
| `/stores` | Store list + health | Add store | 4 stat cards + table (name, platform, raw lowercase status badge, "Health 92", last sync/activity) | **Two surfaces for one entity** (`/stores` vs Shopify card on `/settings/integrations`); "Add store" creates a `manual/shopify/woocommerce` record via `POST /stores` without OAuth — a second, confusing path next to "Connect Shopify"; no row actions; unexplained health score; error rows offer no remedy | P2 |
| `/settings` | Hub | — | 2×2 cards: Integrations, Global Rules, Profile/Team/Billing marked unavailable | Fine; billing/profile absent (no backend) | P3 |
| `/notifications` + top-nav menu | Alerts | Mark read | Panel + menu with unread badge | Kinds are plain codes in places (`kind` shown raw in dashboard activity list) | P3 |
| `/orders`, `/orders/[id]`, `/shipments` | Fulfilment | View | Working tables; shipments merges two queries client-side | Out of dashboard scope; keep. Shipments "Last synchronisation: Never" wording on Home is order-centric noise | P3 |
| `/inventory`, `/pricing`, `/automation`, `/settings/global-rules` | Operations | Various | Working pages | Out of scope; nav placement reviewed in §E | P3 |
| `/analytics` | Reporting | Period select | Same payload as Home with a native `<select>` period; `USD` hard-coded | Keep as the reporting page; Home should stop duplicating it | P2 |
| `/customers`, Suppliers | Placeholders | — | `ComingSoon` page; nav shows "SOON" | Fine mechanism; count of placeholders in primary nav (2) adds noise | P3 |
| Shell (sidebar/top nav/mobile drawer) | Wayfinding | — | 6 sections, 14 items; at 900 px height the last items ("Automation", Notifications, Settings) fall below the fold behind the Collapse bar with no scroll affordance (`desktop-light-dashboard.png`); Search and Help are permanently disabled icons; mobile drawer mirrors the full list | Too many top-level destinations for the fold; technical grouping (Sales / Stores / Analytics / System) rather than merchant jobs; disabled placeholders in the top bar | **P1** |
| Global states | — | — | `EmptyState`, `ErrorState`, `Skeleton`, `ComingSoon`, `app/loading.tsx`, `(protected)/loading.tsx`, `error.tsx`, `not-found` all exist and are used | Feedback after mutations is inline `Alert` only — **no toast**; loading skeleton shapes do not match final layouts (5 bars for a table) | P2 |
| Dark mode | — | — | Fully tokenised; renders consistently (dashboard, drafts, integrations, editor captured) | Chart series colours are fixed hex (not tokens); status badge contrast in dark not measured | P3 |

## D. UX Findings

Severity reflects merchant impact, not taste.

| ID | Severity | Finding | Evidence |
|---|---|---|---|
| F-1 | **Critical → closed in UX-L2D-04** | Published products were unreachable: `/products` linked to `/products/{id}`, which had no route → 404. Closed by adapting the reviewed historical page under UX-L2D-GATE-04 (`catalogue.spec.ts`, `product-route-isolation.spec.ts`) | `product-table.tsx:157`, capture probe `products-row-link`, screenshot |
| F-2 | **High** | Home dashboard is an analytics wall, not an operations surface: no drafts, readiness, failures, integration health, or next action; identical payload to `/analytics`; empty workspace shows 14 zeros | `dashboard/page.tsx`; `desktop-light-dashboard{,-EMPTY}.png` |
| F-3 | **High** | Drafts list cannot be operated at scale: no search/sort/filter/pagination (API supports `q`, `sort_by`, `page`), no thumbnails, redundant status column, action clutter, actions off-screen on mobile | `product-table.tsx:88`; `desktop-light-drafts.png`, `mobile-light-drafts.png` |
| F-4 | **High → closed in UX-L2D-05** | Post-publish state contradiction: after a successful publish the header still read "Draft · Draft saved — not live · Choose a store". Closed by one lifecycle derivation (`lib/editor-lifecycle.ts`) feeding header, panel and actions; the header now reads "Added to Shopify · Saved in DropPilot · {shop}" with View product / Back to products (`editor-lifecycle.spec.ts`) |
| F-5 | **Medium** | Inconsistent page gutters: `/drafts`, `/products`, `/imports/history` and the editor content render flush against the sidebar and viewport edges; other pages use `p-4 sm:p-6` | screenshots at all three widths; `main` horizontal-overflow probe true on editor routes |
| F-6 | **Medium → Home closed in UX-L2D-03; Analytics neutralised in UX-L2D-07** | Revenue was hard-coded to `USD`/`$`. Home shows no money (-03). Analytics shows figures as recorded with no symbol and says why (-07); a truthful symbol needs a currency in the analytics payload (BACKEND DEPENDENCY — ANALYTICS CURRENCY) | `analytics/page.tsx`, `charts/chart-theme.ts` |
| F-7 | **Medium** | Navigation: 14 destinations in 6 technical sections; last three items below the fold at 900 px with the Collapse bar occluding them; two "SOON" items and two permanently disabled top-bar icons (Search, Help) | `lib/navigation.ts`; `desktop-light-dashboard.png` |
| F-8 | **Medium → closed in UX-L2D-06** | Store entity was split across `/stores` (manual "Add store" without OAuth) and `/settings/integrations`. Integrations is now canonical; `/stores` is a record view with the shared vocabulary; the manual dialog is removed from the UI (`lib/channel-state.ts`, `channels.spec.ts`) |
| F-9 | **Medium → closed (overflow in UX-L2D-05, placeholders in UX-L2D-07)** | Editor tab strip overflowed with no affordance; now edge fades, chevrons from `md`, selected tab scrolled into view. The `AI tools` and `History` placeholder sections were removed from navigation in -07 | `product-editor-tabs.tsx`; `editor-lifecycle.spec.ts`, `editor-foundation.spec.ts` |
| F-10 | **Medium (partly addressed in UX-L2D-05)** | Two readiness vocabularies in the editor. -05 makes the *lifecycle* vocabulary single-sourced and separates "Validation passed" (server readiness) from "Published" (listing); the client checklist heuristic (`readiness.ts`) still exists as advisory "Items to review" and is not merged with server blockers | `editor-header/readiness.ts`, `publish-checklist.tsx` |
| F-11 | **Low** | No toast/feedback primitive; mutation results are inline alerts that can render off-screen; no `Select`, `Tabs`, `Checkbox`, `Switch`, `Pagination` primitives (13 native `<select>`s) | `components/ui/` listing |
| F-12 | **Low** | Skeletons do not mirror final layout (five 56 px bars for a 9-column table); stat cards show "Loading" text | `product-table.tsx:96`, `dashboard/page.tsx` |
| F-13 | **Low** | Notification `kind` codes (e.g. `import_completed`) rendered raw in the Home activity list | `dashboard/page.tsx:186` |
| F-14 | **Low → partly addressed in UX-L2D-07** | `ux-l2d-hardening.spec.ts` probes every accepted screen for one `h1`, a `main` landmark, named controls, heading outline, keyboard reachability of the primary control and overflow at five widths; `/stores` health numbers removed (-06). No axe/contrast tooling (dependency not requested); contrast not measured | `tests/e2e/ux-l2d-hardening.spec.ts` |
| F-15 | **Info** | `recharts` 2.x is end-of-line (npm deprecation warning); `next@15.1.6` carries an npm-reported CVE notice — dependency upgrades are **out of Phase 2 scope**; recorded for the owner | `npm ci` output |

## E. Proposed Information Architecture

Organise by merchant job, keep every current route reachable, expose nothing that does not exist. Counts in the sidebar stay real (`workspace-counts`).

```
Home                          /dashboard          (operational home, §G)
Products
  Drafts            (n)       /drafts
  Published         (n)       /products
  Import history              /imports/history
Sales
  Orders                      /orders
  Shipments                   /shipments
Channels                      /settings/integrations   ← primary "connect / fix" surface
  Stores                      /stores                  ← per-store health & sync settings (secondary)
Automation
  Rules                       /automation
  Pricing                     /pricing
  Inventory                   /inventory
  Global rules                /settings/global-rules
Reports                       /analytics
Settings                      /settings   (Integrations, Global rules, Profile/Team/Billing when they exist)
Notifications                 top bar only (menu + /notifications page)
```

Decisions and reasoning:

- **Home replaces "Dashboard"** as the first item and becomes an operations page; reporting moves to **Reports** (`/analytics`) so the two stop duplicating each other (F-2).
- **Products** becomes one group with Drafts first: the import → draft → publish → published lifecycle is the product's core loop and should read top-to-bottom. "Import" is an action on Drafts, not a destination; "Import history" stays as the audit trail.
- **Channels** is the merchant word for Shopify/eBay/AliExpress connections; it points at the page that can actually connect and repair. `/stores` remains for per-store sync settings/health but is demoted to a child, and its manual "Add store" is moved behind the Channels flow in UX-L2D-06 (F-8). This scales to eBay/TikTok Shop/WooCommerce/Etsy without new top-level items.
- **Automation** groups the three rule-driven pages (automation rules, pricing, inventory, global rules) that today sit in three different sections.
- **Coming-soon items leave the primary nav** (Suppliers, Customers). The `status: "coming-soon"` mechanism is kept for the `ComingSoon` pages, which remain reachable by URL; a small "Planned" note can live on Settings.
- Sections collapse from 6 to 5 and items above the fold from 14 to ~10; the sidebar gets a scroll fade so nothing is silently occluded (F-7).
- Top bar: remove the permanently disabled Search/Help icons until they work (F-7); keep Notifications, theme, user menu; add a breadcrumb/page title that reflects nested routes (`Settings › Integrations`).
- AI / Optimization does **not** get a top-level entry: today's capability is `POST /products/{id}/optimize` with a stub provider and a version history sheet — a per-product action, not a workspace.

## F. Proposed Design System

Evolve, don't replace. Every item below reuses the existing HSL tokens and Radix primitives.

| Area | Proposal |
|---|---|
| Typography | Keep Inter. Fix a 5-step scale used everywhere: page title `text-2xl/semibold`, section `text-lg/semibold`, card title `text-base/semibold`, body `text-sm`, meta `text-xs text-muted-foreground`; `tabular-nums` on every numeric column and stat |
| Spacing | 4-px grid via Tailwind; **one `PageContainer`** primitive (`p-4 sm:p-6`, `max-w-screen-2xl`, `space-y-6`) applied to every protected page so F-5 cannot recur; card padding `p-5`; table cell `px-4 py-3` |
| Page widths | Content max 1400 px centred (Tailwind `container` config already says 1400); editor keeps full width with an 8-px gutter minimum |
| Surfaces | `bg-background` canvas, `bg-card` cards with `border` and `rounded-lg`; no shadows beyond `shadow-sm`; no gradients/glass |
| Buttons | Existing `Button` variants only: one `default` (primary) per view region, `outline` secondary, `ghost` tertiary, `destructive` confirmed via dialog. Row-level actions collapse to one visible action + overflow menu |
| Status language | One `StatusBadge` primitive with fixed semantic variants: `neutral` (Draft), `info` (Publishing/Checking), `success` (Published/Connected), `warning` (Needs attention/Webhook setup), `danger` (Failed/Disconnected). Text always present (never colour-only). Vocabulary table lives in one module (`lib/status-language.ts`) and is reused by Home, Drafts, Products, Channels, editor |
| Tables | `DataTable` wrapper over the existing `Table`: header, optional selection column, sortable header cells (sort via API `sort_by`), sticky first column on mobile **or** card list below `md`, pagination footer bound to `meta`, matching skeleton rows |
| Forms | Existing `form.tsx`/`input`/`label`; add `Select` (Radix) to replace the 13 native selects incrementally, `Checkbox`, `Switch`; help text and error text patterns fixed |
| Dialogs / drawers | Keep `Dialog` and `Sheet` (mobile bottom sheet already proven in the editor); destructive confirmations always in a `Dialog` with the consequence stated |
| Feedback | Add a `Toast` region (Radix Toast) for mutation results; keep inline `Alert` for persistent conditions; add `aria-live` for autosave state (editor already has one) |
| Empty / loading / error | Keep `EmptyState`/`ErrorState`; every list gets a purposeful empty state with the next action; skeletons mirror the final layout |
| Icons | Lucide only, 16 px in rows, 20 px in headers; no decorative icons in stat cards unless they carry meaning |
| Responsive | Breakpoints as today (`md` sidebar, `sm` header stacking). Tables → card lists on mobile; primary page action in the header stays reachable; editor keeps the sticky action bar |
| Dark mode | Existing token pairs; add chart colour tokens (`--chart-1..4`) so Recharts stops using fixed hex; verify contrast of `success`/`warning` badges in dark (F-14) |
| Motion | Only what exists (sidebar width, accordion); honour `prefers-reduced-motion` as today |

## G. Dashboard Home Proposal

Home answers the six merchant questions with data that already exists. Each block lists its data source and whether it is real today.

| Block | Answers | Data source | Real today? |
|---|---|---|---|
| **Needs attention** (top, only when non-empty) | What failed / what needs me | `GET /products/imports?status=failed` (failed imports with Retry); `GET /integrations/{shopify,aliexpress}/status` (expired token, `webhookHealth: degraded`, `lastError`); `GET /orders/statistics.failedSyncsLast7Days`; unread `sync_failed`/`automation_failed`/`webhook_failure` notifications | **Yes** — all four endpoints exist; composition is client-side |
| **Your products** (three tiles with counts and links) | What am I working on / ready to publish | Drafts count and Published count from `GET /products/workspace-counts`; "Ready to publish" — **not available** (readiness is a per-draft `POST publish-readiness`) | Drafts/Published **yes**; "Ready" **no → future backend dependency** (a `readiness` summary or a cached readiness flag on the list row). Until then the tile reads "Review drafts" without a ready count |
| **Continue where you left off** | What am I working on | `GET /drafts?sort_by=updated_at&sort_dir=desc&size=5` — title, updated time, AI status, Edit link | **Yes** |
| **Recently published** | What went live | `GET /products?sort_by=updated_at&sort_dir=desc&size=5`; per-row listing state needs `GET /drafts/{id}/listings` (N calls) or a listing summary on the list row | Titles **yes**; "visible on shop" state **partial** (per-row fetch) → prefer a backend list-row field later |
| **Channels** | Are my stores connected | Shopify/AliExpress/eBay status endpoints; `GET /stores/statistics` | **Yes** — reuse the integration cards' status language |
| **Next step** (single call-to-action) | What should I do next | Derived client-side: no channel → "Connect Shopify"; channel but 0 drafts → "Import your first product"; drafts with failed AI/blockers → "Review drafts"; otherwise "Publish ready drafts" | **Yes** (rule-based, no fake AI) |
| **Recent activity** | What happened | `analytics.recentActivity` (already returned) with human labels for `kind` | **Yes** |
| Order sync tiles and charts | — | Move to Reports (`/analytics`); keep a compact "Orders: n pending fulfilment" link tile on Home only if `totalOrders > 0` | Yes (relocation) |

Rules: no metric without an action or link; empty workspace shows a 3-step onboarding checklist (Connect channel → Import → Publish) instead of zeros; currency from `identity.tenant.defaultCurrency` (F-6); period selector stays on Reports only.

## H. Drafts Proposal

Lifecycle vocabulary must map to real states. What exists: `Product.status` (`draft | active | archived | unavailable`), `aiStatus` (`not_optimized | optimized | failed`), `StoreListing.status` (`synced | error | …`) + `onlineStorePublished`, server readiness (`canPublish`, blockers, recommendations), import records. Proposed **derived** labels (client, one module): `Imported` (no edits since import, `updatedAt === createdAt`), `Editing` (edited, not published), `Needs attention` (`aiStatus: failed`, `lastSyncError`, or a cached blocker), `Publishing` (listing pending), `Published` (listing `synced` → leaves Drafts). "Ready" is only shown when a readiness check has been run for that draft in this session — never inferred.

Workspace:

- **Toolbar:** search (`q`), sort (`updated_at`, `created_at`, `title`, `cost_price_min` — all API-supported), AI-status chip filter (client-side on the page, since the API has no filter param), "Import as Draft" as the single primary action.
- **Rows:** thumbnail (needs `imageUrl` on the list row — **backend dependency**; until then a consistent placeholder), title (2 lines) + supplier ID/source, lifecycle badge, supplier cost range, stock, variants, AI badge, one primary button "Edit" + overflow (Optimize, History, Preview, Refresh supplier, Publish → opens editor on the Publish tab).
- **Selection:** checkbox column with a bulk bar — only for actions the API supports today per item (Optimize ×n via repeated `POST /optimize`, Refresh ×n). Bulk publish is **not** offered (single-draft publish endpoint with readiness gate).
- **Pagination:** footer bound to `meta.totalItems/hasNext`; 25 per page default.
- **States:** empty (import CTA + link to Channels if none connected), loading skeleton rows, error with retry, per-row failure hint linking to Import history.
- **Mobile:** card list (thumbnail, title, badge, cost, Edit) instead of a horizontally scrolling table.
- Drafts vs Products boundary unchanged: the backend predicate (no synced listing) decides membership; the UI never moves an item itself.

## I. Product Editor Proposal

Non-negotiables preserved verbatim from `draft-product-editor.tsx`: `expectedUpdatedAt` on every PATCH, 409 → `enterConflict`, conflict phases `detected → reload-confirm | reviewing`, autosave gated by `dirty`/`isConflicted`, `dirtyEpochRef` guard, review-dialog "stale" detection, two-editor safety. These are not touched by Phase 2; UX-L2D-05 changes layout only and must keep `draft-editor-concurrency.spec.ts`, `editor-foundation.spec.ts`, `editor-header.spec.ts`, `publish-integrity.spec.ts` green.

Architecture:

- **Header** (keep): identity, lifecycle badge (single vocabulary from §H; fix the post-publish contradiction, F-4), save-state indicator (`Saving… / Saved / Unsaved changes / Conflict`) with `aria-live`, Preview / Review & publish / More.
- **Section navigation:** replace the 11-tab strip with a **left rail on ≥ lg** (grouped: Product · Selling · Improve · Publish) and a horizontally scrollable pill row with edge fade below `lg`. Placeholder tabs `AI tools` and `History` move out of primary navigation: AI actions live in "More" and the SEO/Improve section; History stays as the existing sheet. Sections rendered only for domains that exist: details, description, images & video, options & variants, price & profit, stock, shipping, search & SEO, review & publish.
- **Right rail "Before you publish":** single readiness model — server blockers/recommendations when a store is selected, client heuristics only as "suggestions" and never as "Blocked" (F-10).
- **Content gutters:** `PageContainer` (F-5). Sticky mobile action bar and bottom sheet stay.
- **Feedback:** toast for "Saved"/"Published" in addition to inline; destructive actions (remove image, disable variant) keep confirmation dialogs.
- Publish success panel: lifecycle badge flips to Published with store link; "View in Products" points at the (to-be-restored) product detail page.

## J. Phase 2 Execution Backlog

Sequence refined after inspection. Each milestone is one PR-sized change on `feature/ux-l2d-dashboard` (or a child branch per milestone), stacked on `c0092da6`; all keep `npm run lint && npm run typecheck && npm run build` and the relevant Playwright specs green; Phase 1 provider isolation and M2A concurrency specs are always in the run set.

| # | Milestone | Scope | Files / areas | Acceptance criteria | Tests | Risk |
|---|---|---|---|---|---|---|
| UX-L2D-01 | Baseline + audit (this report) | Docs only | `docs/ux/ux-l2d-01-baseline.md`, CHANGELOG/ROADMAP/ledger state corrections (Phase 1 acceptance, UX-L2B in develop, historical UX-L2C, UX-L2D naming, GATE-04) | Report delivered; owner decisions in §L taken; this commit | none | — |
| UX-L2D-02 (**done**) | Application shell | New IA (§E), `PageContainer`, sidebar scroll fade + pinned Settings, breadcrumb, remove disabled Search/Help. Deferred to the milestone that needs them: `StatusBadge`/`status-language`, `Toast`, chart tokens | `lib/navigation.ts`, `components/navigation/*`, `layouts/app-shell.tsx`, `components/ui/{page-container,status-badge,toast,select}.tsx`, `app/globals.css`, every protected `page.tsx` (wrap in `PageContainer`) | All 18 protected routes reachable from nav or a parent; no route 404s from nav; every page has the same gutter at 390/1024/1440; sidebar shows all items at 900 px height; dark mode unchanged; `shell.spec.ts`, `smoke.spec.ts`, `auth.spec.ts` pass; screenshots at three widths | Extend `shell.spec.ts` (nav inventory, gutters, mobile drawer); new `ux-l2c-shell.spec.ts` | Nav test fixtures reference old section labels; `findNavItem` longest-prefix rule must keep working for nested routes |
| UX-L2D-03 (**done**) | Dashboard Home | Blocks in §G with real data; onboarding empty state; no money on Home (F-6 — Reports keeps the caveat); order tiles/charts stay on Reports and Orders | `app/(app)/(protected)/dashboard/page.tsx`, new `components/home/*`, `services/dashboard.ts` (composition hooks only), `analytics/page.tsx` | Six questions answered from real endpoints; no invented metric; empty workspace shows the checklist; every tile links; revenue shows tenant currency; mocked and live (CI) renders | New `home.spec.ts` (mocked API states: empty, attention, healthy); `phase6-ops.spec.ts` if it asserts dashboard text | "Ready to publish" count unavailable → tile wording without a count until the backend adds a summary (future dependency, logged) |
| **UX-L2D-GATE-04** | Mandatory independent review of `origin/feature/ux-l2c-live-state-clarity` (`ece8322`) against its true base (`develop` @ `a543b029`) before UX-L2D-04 starts: feature by feature — accept as-is / adapt / selectively integrate / supersede by UX-L2D / reject — with behavioural verification, not diff size or labels | review only | Written verdict per feature incl. `/products/[productId]`, lifecycle labels, vitest, Drafts/editor-header/integrations changes | Runs the branch's own suites where possible | Blocks -04/-05/-06; -02/-03 proceed |
| UX-L2D-04 (**done**) | Catalogue / Drafts / product page | Product page adapted from historical UX-L2C (Critical F-1 closed); toolbar (search/sort), server pagination, URL sync, status words, single primary action + icon secondaries + overflow, card list on mobile. Not built: bulk selection (no bulk endpoint), lifecycle filter (no API parameter), thumbnails (no list-row image) | `components/products/product-table.tsx` (split into `drafts-table.tsx`/`products-table.tsx`), `components/ui/data-table.tsx`, `services/drafts.ts` (query params), `lib/product-lifecycle.ts` (from the live-state branch if adopted) | Search/sort/pagination round-trip to the API; boundary unchanged; mobile card list without horizontal scroll; empty/loading/error states; `products.spec.ts`, `product-optimization.spec.ts` pass | New `drafts-workspace.spec.ts` (mocked + live) | Overlaps the live-state branch's `product-table.tsx` edits; thumbnails need a backend list field (dependency, not built here) |
| UX-L2D-05 (**done** as lifecycle clarity) | Product editor lifecycle clarity | Delivered: one lifecycle derivation (header badge, save state, next action, post-publish, Review & publish), F-4 closed, F-9 overflow closed (scrollable strip with fades/chevrons/scroll-to-selected rather than a left rail), conflict focus/dialog focus return, mobile bar. Not delivered from §I: left rail, placeholder tabs out of primary nav, toast feedback, merging the client checklist heuristic with server blockers | `components/drafts/editor-header/*`, `draft-product-editor.tsx` (layout only), `review-publish-panel.tsx`, `publish-checklist.tsx`, `readiness.ts` | All M2A specs unchanged and green (`draft-editor-concurrency`, `draft-editor-real-conflict`, `editor-foundation`, `editor-header`, `publish-integrity`); tab/section URLs unchanged (`?tab=`); no gutter/overflow at 390/1024/1440; visual matrix re-captured | Existing five editor suites + `ux-l2b-r3-visual.spec.ts`; new section-nav spec | Highest regression risk; do it after -04 and only as layout changes to the 1546-line component (no state-logic edits) |
| UX-L2D-06 (**done**) | Channels + store states | Delivered: Integrations canonical with an overview strip; `/stores` demoted to records (manual Add store removed rather than kept behind a secondary path — it produced unusable rows); one vocabulary in `lib/channel-state.ts` (Checking / Status unavailable / Setup unavailable / Not connected / Awaiting authorization / Connected / Needs attention / Reconnect required) with a next action per state; disconnect confirmations. Not done: Home's own channel summary still uses its -03 wording (Home out of -06 scope) | `settings/integrations/page.tsx`, `components/integrations/*`, `components/stores/*`, `stores/page.tsx` | Every state the three status endpoints can return has a text label and a next action; no green-dot-only states; secrets never rendered; `integrations.spec.ts`, `shopify-oauth.spec.ts`, `store-status-label` tests pass | Extend `integrations.spec.ts`; new mocked state-matrix spec | Live-state branch touches `integrations.spec.ts` |
| UX-L2D-07 (**done**) | Cross-app hardening | Delivered: journey audit (1–6), semantics/keyboard/overflow probe on the 8 screens at 5 widths, placeholders removed, analytics currency neutralised, vocabulary aligned, dead code removed, two duplicate/polling requests removed, timing-sensitive tests made deterministic. Not done: `Select` primitive, `recharts` colour tokens, axe/contrast tooling, layout-shift measurement | app-wide | see the UX-L2D-07 report | `ux-l2d-hardening.spec.ts` + full suite | — |

Non-code dependencies to log for later backend phases (not built in Phase 2): list-row `imageUrl`/thumbnail; readiness summary or cached `canPublish` per draft; listing summary per product row (`visibleOnShop`, store name); `currency` in the analytics payload (or client uses tenant currency — chosen for now).

## K. Explicit Non-Goals

Phase 2 will **not**: merge or undraft PR #7; deploy; touch `main`/`develop` directly; add migrations or change any schema; add or upgrade dependencies without owner approval (incl. `recharts`, `next`); change backend endpoints or add new ones (dependencies are logged, not built); alter M2A concurrency logic (`expectedUpdatedAt`, 409 handling, conflict phases, autosave gating, review flow); change auth, CSP, cookies or the Phase 1 provider-isolation fixture; change CI triggers/permissions, scanners or tests to get green; implement Suppliers, Customers, Profile/Team/Billing, global search, help centre, real AI providers, bulk publish, or new sales channels; invent analytics, statuses or AI claims; read real `.env`, production data, provider accounts or paid systems; fix the deferred Celery multi-worker/exclusive-pidbox staging item (carried in the risk register as a separate safe-staging validation); perform unrelated infrastructure cleanup.

## L. Final Recommendation

**UX-L2D-02 (application shell) is ready to begin** on `feature/ux-l2d-dashboard` stacked on `c0092da6`. Owner decisions taken on 2026-09-15:

1. **Base branch:** approved — stacked on the accepted Phase 1 SHA; no rebase onto `develop` while it differs from that baseline; no push without separate authorisation; PR #7 not merged by this programme.
2. **Naming:** renamed to UX-L2D-01…07; the historical branch keeps its UX-L2C identity.

**UX-L2D-GATE-04** (owner-mandated): before UX-L2D-04, an independent, behaviourally verified review of `feature/ux-l2c-live-state-clarity` decides per feature between accept / adapt / selectively integrate / supersede / reject. It must not be blindly merged, cherry-picked, abandoned or rewritten. Finding F-1 (`/products/[productId]` 404) stays **open and Critical** until that gate resolves ownership; UX-L2D-02 must not add a second product-detail route.

Until the owner answers, nothing is committed: the Phase 2 worktree is clean at `c0092da6`, the branch is local only, no code has changed, and the two UX-L2D-01 documentation files (repo `docs/ux/ux-l2c-01-baseline.md` + the Phase 1 acceptance note in CHANGELOG/ROADMAP) will be the first commit once the plan is approved.
