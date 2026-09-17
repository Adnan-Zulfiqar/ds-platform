# Frontend architecture

How the Next.js application is organised, and why. Current as of Phase 2.

## Contents

1. [Directory layout](#directory-layout)
2. [Component organisation](#component-organisation)
3. [Routing](#routing)
4. [The application shell](#the-application-shell)
5. [Navigation](#navigation)
6. [State management](#state-management)
7. [Data access](#data-access)
8. [Theming](#theming)
9. [Responsive design](#responsive-design)
10. [Accessibility](#accessibility)
11. [Mock data](#mock-data)
12. [Known limitations](#known-limitations)

---

## Directory layout

```
frontend/
├── app/                      App Router — routes only
│   ├── (auth)/               Public: login, register, forgot-password
│   ├── (protected)/          Authenticated: shell + application routes
│   ├── unauthorized/         403 — deliberately outside (protected)
│   ├── error.tsx             Root error boundary
│   ├── not-found.tsx         404
│   └── layout.tsx            Fonts, providers, skip link
├── layouts/                  Structural composition
│   └── app-shell.tsx         Sidebar + top bar + scrolling main
├── components/
│   ├── ui/                   Design system primitives — no business logic
│   ├── navigation/           Sidebar, drawer, top bar, menus
│   ├── dashboard/            Stat cards, chart frame
│   │   └── charts/           Individual charts + shared chart theme
│   └── auth-guard.tsx        Client-side route protection
├── features/                 Reserved — feature modules land here
├── hooks/                    Reusable behaviour
├── lib/
│   ├── navigation.ts         The navigation manifest
│   ├── api-client.ts         The single HTTP client
│   ├── auth/                 In-memory token store
│   ├── validation/           Zod schemas
│   └── mock/                 Quarantined placeholder data
├── providers/                React context providers
├── services/                 Data access — API calls and query keys
├── stores/                   Zustand — UI state only
├── types/                    Shared types
└── tests/e2e/                Playwright
```

### Why `layouts/` and `components/navigation/` are separate

`layouts/` composes; `components/` supplies the pieces. `app-shell.tsx` decides
that the sidebar sits left of a column containing the top bar and a scrolling
main region. It does not decide what a navigation item looks like.

The split matters because a second shell is plausible — a focused checkout or
onboarding flow with no sidebar — and it should reuse the navigation components
without inheriting the dashboard's frame.

### `features/` is empty on purpose

Reserved for feature modules (`features/products/`, `features/orders/`) that own
their own components, hooks, and types. Nothing lives there yet because no
feature exists, and creating a directory structure before there is code to put
in it is guesswork about a shape that has not been designed.

---

## Component organisation

Three tiers, distinguished by what they are allowed to know:

| Tier | Location | May know about |
|---|---|---|
| **Primitives** | `components/ui/` | Nothing. No API types, no routes, no stores |
| **Composed** | `components/navigation/`, `components/dashboard/` | Application concepts — routes, the session, stores |
| **Layouts** | `layouts/` | How composed components fit together |

A primitive that imports `useAuth` has left its tier. That rule is what keeps
`components/ui/` reusable rather than quietly coupled to this application.

### Current primitives

`alert` · `avatar` · `badge` · `button` · `card` · `coming-soon` · `dialog` ·
`dropdown-menu` · `empty-state` · `error-state` · `form` · `input` · `label` ·
`page-header` · `separator` · `sheet` · `skeleton` · `table` · `tooltip`

### Empty, error, and loading are three different states

Kept as three components rather than one, because conflating them produces the
worst failure in a dashboard: a request fails, and the user is told they have no
products.

* `Skeleton` — still loading. Mirrors the real layout so nothing jumps.
* `EmptyState` — succeeded, nothing there. Offers the next step.
* `ErrorState` — failed. Announced via `role="alert"`, shows the request id.

---

## Routing

Route groups shape the layout tree without appearing in the URL.

| Group | Layout | Routes |
|---|---|---|
| `(auth)` | Centred card, no chrome | `/login`, `/register`, `/forgot-password` |
| `(protected)` | `AuthGuard` + `AppShell` | `/dashboard`, `/products`, `/stores`, `/orders`, `/analytics`, `/settings` |
| *(none)* | Standalone | `/unauthorized`, `/`, 404 |

**`/unauthorized` sits outside `(protected)` deliberately.** It reports a
permission failure to a user who *is* signed in. Inside the protected group it
would have to pass the guard it exists to report on, and treating it as
authentication-protected would bounce the user to sign-in, where they would
enter correct credentials and arrive back at the same wall.

### Every route exists before its feature does

`/products` and its siblings render `ComingSoon`. They are real routes so
navigation never 404s, and they state plainly that the feature is not built
rather than showing an empty table that implies it works.

---

## The application shell

```
┌──────────┬────────────────────────────┐
│ Sidebar  │ Top navigation             │
│          ├────────────────────────────┤
│          │ Main content (scrolls)     │
└──────────┴────────────────────────────┘
```

**Scrolling is confined to `main`, not the document.** The sidebar and top bar
stay put without `position: fixed`, which avoids content sliding underneath and
avoids mobile browsers mismeasuring the viewport as their address bar collapses.

`AuthGuard` wraps the shell in `(protected)/layout.tsx`, so a route added to
that group is protected by default — the safe thing happens without anyone
remembering to do it.

**The page gutter lives in the layout, not the page** (UX-L2D-02). The same
layout wraps every page in `PageContainer` (`components/ui/page-container.tsx`:
`p-4 sm:p-6`, centred, capped at the `2xl` screen width). Pages own their
vertical rhythm and nothing else; before this, each page carried or forgot its
own padding, and Drafts, Products, Import history and the editor rendered flush
against the sidebar while their neighbours had a gutter.

---

## Navigation

`lib/navigation.ts` is the single source of truth. The desktop sidebar, the
mobile drawer, and the top bar's breadcrumb all read from it.

Sections are merchant jobs, not subsystems (UX-L2D-02): Home · Catalogue
(Drafts, Products, Import history) · Sales · Channels (Integrations, Stores) ·
Automation (Rules, Pricing, Inventory, Global rules) · Reports. Settings is
pinned below the scrolling list (`NAV_FOOTER_ITEMS`) so it stays reachable at
any viewport height; the list itself scrolls inside `NavScrollRegion`, which
fades whichever edge is hiding items. Items whose href is a prefix of other
items' hrefs (`/dashboard`, `/settings`) carry `exact: true` so the parent does
not light up on a child.

Each item carries a `status`:

* `ready` — renders as a link.
* `coming-soon` — renders as a non-interactive `div` with a "Soon" badge and
  `aria-disabled`.

A `coming-soon` item is **not** a disabled link or button. A disabled
interactive element is still reachable by keyboard and then does nothing, which
is more confusing than an element that was never interactive. Since UX-L2D-02
no such item is in the primary manifest: planned destinations (Suppliers,
Customers) live in `PLANNED_NAV_ITEMS`, not in the sidebar, because a
placeholder in primary navigation advertises a capability the product does not
have. `/customers` still serves its `ComingSoon` page by URL.

Desktop and mobile render the same `SidebarNav` component. Duplicating the list
into a separate mobile component is the usual approach and the usual source of a
route that exists on one and not the other.

### Desktop / mobile split is CSS, not JavaScript

The sidebar is `hidden md:flex`; the drawer trigger is `md:hidden`. Both are
present in the server-rendered HTML, so the correct one shows before hydration.
A JavaScript viewport check would render the wrong navigation for a frame.

`useIsDesktop` exists only for behaviour CSS cannot express — closing the drawer
when the viewport grows past the breakpoint.

---

## State management

| Concern | Owner | Why |
|---|---|---|
| Server data | React Query | Caching, refetching, invalidation already solved |
| Session identity | `AuthProvider` context | Resolved once before the first render decision; every consumer needs the same instance |
| UI state | Zustand (`ui-store`) | Sidebar collapse, drawer open |
| Notifications | Zustand (`notification-store`) | Structure only; migrates to React Query when a source exists |

**Server data never goes into Zustand.** Copying an API response into a store
creates two competing sources of truth, which is the most common way a React
codebase decays.

`ui-store` persists only `sidebarCollapsed`. The mobile drawer's open state is
deliberately excluded — restoring it as open on the next visit would be wrong.

---

## Data access

`services/` owns the endpoint, the query key, and the response type together, so
a change to any of the three is a single-file edit. Components never call
`fetch` or `apiClient` directly.

Query keys are hierarchical (`["products", "list", query]`) so invalidating
`productKeys.lists()` clears every list without touching cached detail records.

### Services that exist as structure only

`dashboard.ts`, `products.ts`, and `stores.ts` define query keys and types but
**no fetchers** — those endpoints are registered routers with no routes.
Implementing them would produce code that compiles, looks finished, and 404s.

### Naming

Modules are `services/auth.ts`, not `services/auth.service.ts`. The Phase 2
brief used the latter as an example; the existing convention was already
established in Phase 1, and consistency across the directory is worth more than
matching an illustration. Recorded here so the deviation is deliberate rather
than accidental.

---

## Theming

Light, dark, and system, via `next-themes` writing a class onto `<html>` from an
inline script that runs **before first paint** — a React effect would render the
light theme for one frame first, which is very obvious to anyone using dark
mode.

Every colour is a CSS variable holding bare HSL channels
(`--primary: 221 83% 53%`), so Tailwind can compose them with alpha modifiers:
`bg-primary/50` only works if the variable is channels-only.

**Charts read the same variables at runtime.** Recharts renders SVG and takes
colours as props, so `components/dashboard/charts/chart-theme.ts` passes
`hsl(var(--primary))` into `fill` and `stroke`. A theme switch therefore
recolours the charts with no JavaScript — the variables themselves change.

Dark mode uses a very dark blue-grey rather than pure black: black plus bright
text causes halation, the main source of eye strain in dark interfaces used for
long sessions.

---

## Responsive design

Verified at **320px, 768px, and 1440px** by Playwright, including an explicit
assertion that the document does not scroll horizontally at 320px — the most
common responsive failure and invisible on a desktop screen.

| Breakpoint | Behaviour |
|---|---|
| `< 768px` | Sidebar hidden, drawer from the top bar, single-column grids |
| `768px+` | Sidebar visible, two-column stat grid |
| `1024px+` | Three-column stat grid, side-by-side charts |

Wide content scrolls inside its own container, never the page.

---

## Accessibility

Not a checklist item — several decisions above exist for it.

* One `h1` per page, enforced by `PageHeader`.
* A skip link is the first focusable element on every page.
* `aria-current="page"` on the active nav item; colour is never the only signal.
* Icon-only controls carry `aria-label` **and** `sr-only` text. Tooltips are an
  enhancement — they never appear on touch, so they cannot be the only label.
* `role="alert"` on error states and form validation messages.
* Focus rings via `:focus-visible` — visible for keyboard users, absent on mouse
  click. Removing outlines entirely is a serious regression.
* `prefers-reduced-motion` disables animation globally.
* Radix supplies focus trapping, Escape handling, and focus restoration for the
  dialog, drawer, dropdown, and tooltip.

---

## Catalogue and the product page

`/drafts` and `/products` share one component, `components/products/product-table.tsx`
(UX-L2D-04). The backend decides membership — a product with a `synced`
`StoreListing` is a Product, otherwise a Draft — and the pages differ only in
wording, the primary action (`Edit` / `View`) and where a row leads.

**The URL is the list state.** `components/catalogue/catalogue-query.ts` reads
`q`, `sort` and `page` from the search params, validates each against what
the API accepts (sort values are the backend's `sortable_fields`; anything
else falls back to the default), and writes them back — `replace` while a
search is typed, `push` for sort and page changes — so a refresh, a shared
link and Back/Forward all reproduce the list. Search is the server's `q`
(title, external id, supplier name); sort travels as `sort_by`/`sort_dir`
through `services/list-query.ts`; pages come from the server's `meta`. There
is no lifecycle, AI-status or readiness filter because the API has no such
parameter, and a client-side filter over one page would hide matching rows
on other pages. Thumbnails wait for an image field on the list row.

Row status is only what the list response can vouch for: a Products row is
"Published" because the endpoint only returns products with a synced
listing; the store, visibility and sync details need one listings request
per product and live on the product page, never as one request per table
row. Below `lg` the table becomes a card list so the primary action is never
behind a horizontal scroll.

`/products/[productId]` (`components/products/published-product-summary.tsx`)
reads `GET /products/{id}` and `GET /drafts/{id}/listings`, adapted from the
reviewed historical UX-L2C page under UX-L2D-GATE-04 with two changes: a
product whose listings confirm **no synced listing is a draft and is sent to
`/drafts/{id}`** rather than shown as published, and a path segment that is
not a UUID is answered by the route as "Product not found" with no request
made. Missing and foreign-tenant ids read identically (the API answers 404,
never 403). `lib/listing-lifecycle.ts` is the single authority for
"checking / unavailable / not published / added to Shopify / visible on your
shop" — visibility only on `onlineStorePublished === true`, and a failed
listings request is *unavailable*, never *not published*. Outbound links go
through `lib/external-link.ts`: HTTPS on `*.myshopify.com` only, taken from
the server, never built from a handle. Descriptions render as text.

## The product editor's lifecycle

The editor (`components/drafts/draft-product-editor.tsx`) says three things
about a product, and each comes from a different place — so they sit side by
side and never have to hedge each other (UX-L2D-05):

| Axis | Says | Evidence |
|---|---|---|
| Shopify state (the header badge) | Not on Shopify · Added to Shopify · Visible on your shop · Changes not sent to Shopify · Sending to Shopify… · Publish failed · Shopify status unavailable · Checking… | listing rows, this session's publish request/response |
| Save state | Unsaved changes · Saving… · Saved in DropPilot · Couldn't save · Saving paused (conflict) | the editor's own `dirty` / `saveState` / conflict phase |
| Next action | Review & publish · Review N items · Update Shopify · View product · Try publishing again · Resolve conflict | derived from the two above |

All three are derived once per render by `lib/editor-lifecycle.ts`, a pure
function over explicit inputs, and read by the header, the save indicator,
the primary action, the mobile bar, the post-publish panel and Review &
publish. It composes `lib/listing-lifecycle.ts` (the UX-L2D-04 authority
the product page and catalogue use) rather than re-deriving listing facts.
The derivation table lives in that module's header comment; the rules that
matter most:

- **"Visible on your shop" needs `onlineStorePublished === true`.** `false`
  is "Added to Shopify" with a visibility-setup note (Shopify holds it as a
  draft); `null` is "Added to Shopify" with visibility unconfirmed.
- **`lastSyncedAt` only proves "changes not sent".** Price and inventory
  pushes advance it too, so a saved draft *older* than the last sync is still
  only "Added"/"Visible" — nothing ever reads "up to date" or "live".
- **A failed listings request is "unavailable", with a retry.** A failed
  *refresh* of cached listings keeps the last known state and adds a note;
  neither is ever presented as "not on Shopify".
- **The publish response stands in for the listings cache only while the
  cache predates it** (`dataUpdatedAt` vs the response time). Once the
  editor's post-publish refetch lands, the server row wins, including when it
  disagrees.
- **A listing row with `status === "error"` and no synced row is "Publish
  failed"**; the provider's `lastError` text is never shown.

Header controls only navigate; the Review & publish panel's button is the
one that publishes. The panel distinguishes "Validation passed" (the server's
readiness check on the saved draft) from "Published to Shopify" (a listing),
and reads "Update Shopify" once a listing exists. Blocker sections use
`lib/editor-section-labels.ts` so the merchant sees the editor's section
names, not the API's keys.

The M2A concurrency protocol is untouched by all of this: `expectedUpdatedAt`
on every save, 409 → conflict phases (`detected → reload-confirm | reviewing`),
autosave frozen for every phase but `none`, reload-latest and review paths.
UX-L2D-05 changed only presentation around it — the banner takes focus on the
`none → detected` transition (not on every phase change), its dialogs return
focus when they close, manual Save is withdrawn while a conflict is open, and
publishing is disabled with the reason stated.

The section strip scrolls sideways with edge fades, chevrons from `md`, and
the selected tab scrolled into view (a deep link to `?tab=publishing` lands
on Review & publish at 1024). Below `md` the fixed action bar holds Save (only
while there is something to save), an icon-only Preview and the primary
action; the sticky header above keeps the badge and save state visible.

`lib/*.test.ts` are Vitest unit tests for the pure lifecycle and channel-state
modules (`npm run test:unit`, `environment: "node"`). They run locally and are
**not a GitHub CI gate**; Playwright remains the executed coverage there.
Whether Vitest becomes a required gate is a Phase 2 acceptance decision.

## Home

`/dashboard` is the merchant's operations page (UX-L2D-03), not a report. It
answers, in order: what needs attention, what to do next, how big the
catalogue is, what was being worked on, whether channels are healthy, and what
happened recently. Every block reads an endpoint that already exists —
workspace counts, the three integration status endpoints, `GET /drafts` sorted
by `updated_at`, recent imports, order statistics, notifications — and each
block owns its query, so one failed request shows a local error with its own
retry while the rest render.

The derivations are pure functions in `components/home/home-rules.ts`:
channel summaries (a word plus a sentence, never a coloured dot), the
attention list (most severe first, each item linking to the page that fixes
it), and the single next step, decided by ordered rules — no usable Shopify
store → connect one; AliExpress not usable → connect it; no drafts → import
the first product; otherwise continue the latest draft. It is not called a
recommendation because nothing is inferred.

**What Home deliberately does not show.** A "ready to publish" count:
readiness is a per-draft server check with no aggregate, so any count would be
a guess (a backend dependency). Money: the analytics revenue figure sums
`Order.total_amount` across orders in mixed currencies with no FX and no
currency in the payload, so no single label would be true; it stays on
Reports, where that caveat still applies (see Known limitations). A workspace
with nothing connected and nothing imported gets the three-step setup instead
of zero-valued tiles.

The former mock module (`lib/mock/dashboard-data.ts`, `MOCK_*`) was removed
in Phase 6; the analytics charts it fed now live on `/analytics` against
`GET /analytics/dashboard`.

---

## Channels: Integrations and Stores

`/settings/integrations` is the one place a channel is authorized, repaired
or disconnected (UX-L2D-06). `/stores` is a supporting record view. The
decision, from the implementation rather than from taste: only Integrations
can start an OAuth flow (`POST /integrations/{provider}/connect`), read the
provider's own status endpoint, retry Shopify webhooks or delete a
connection; `/stores` reads `GET /stores`, whose rows outlive a disconnect
(the backend deletes the `ShopifyConnection` but keeps the `Store` row with
its products and listings) and whose sync flags nothing consumes. The manual
"Add store" that used to sit on `/stores` is gone from the merchant UI: it
created a `pending` store row with no authorization, which then appeared in
the editor's store list and could never publish. The `POST /stores` endpoint
is untouched.

**One vocabulary.** `lib/channel-state.ts` maps the fields each status
endpoint returns to Checking · Status unavailable · Setup unavailable · Not
connected · Awaiting authorization · Connected · Needs attention · Reconnect
required, with the actions each state supports; every card, the overview
strip and the store-record list read from it. The evidence for each state
is in that module's header. Rules worth knowing:

- A row's existence is never "connected": AliExpress and eBay `connected`
  are the server's computed booleans; a Shopify store whose webhooks were
  never confirmed is "Needs attention" (`webhookHealth === "degraded"`).
- Shopify's `lastError` is `str(exc)` from the sync path and is never
  rendered; AliExpress's `lastError` comes from the backend's exception
  catalogue and is shown; eBay's `reconnectReason` is a machine code mapped
  to a sentence here.
- "Setup unavailable" means the server lacks the provider's app credentials
  — an operator's job, so the card says so and offers no button that would
  fail. Shopify and eBay report this in `configured`; AliExpress has no such
  flag and the card learns it only when `POST /connect` answers 422
  (recorded as a backend dependency).
- Disconnect always confirms, and the dialog lists what the backend really
  does: access removed at the provider, store row kept as Disconnected,
  products and listings in DropPilot kept, nothing deleted from the store.
- Connect/disconnect/retry are admin-only on the server (`RequireAdmin`);
  other roles see the state and a read-only note instead of buttons that
  would 403.

Planned channels (WooCommerce, Etsy, TikTok Shop) are named in one line, not
rendered as cards, so nothing invites a click that goes nowhere. Adding a
real channel is a new `derive*` in `channel-state.ts`, a card built on
`ChannelCard`, and a row in the overview.

## Hardening decisions (UX-L2D-07)

The last Phase 2 milestone was a pass over everything above rather than a
feature. Decisions worth knowing when reading the code:

- **Placeholders are gone from merchant-facing navigation.** The editor's
  `AI tools` section only pointed at the More menu, and `History` announced
  Stage 6 work; both were removed. The real actions remain in the More menu
  (`Improve with AI tools`, `Version history`). A stale `?tab=` deep link to
  either lands on Product details.
- **Analytics money has no currency symbol.** `GET /analytics/dashboard`
  reports totals across stores with no currency field, so the revenue tile
  and the sales chart show plain numbers and the page says why. Nothing
  guesses a currency (BACKEND DEPENDENCY — ANALYTICS CURRENCY).
- **One channel vocabulary everywhere.** Home's channel rows read their
  words from `lib/channel-state.ts`, so "Setup unavailable" means the same
  thing on Home and on Integrations.
- **"live" is not a status word.** The last copy that used it for a synced
  listing ("Draft saved — not live", "live channel listing") was reworded;
  the Global rules "Live Preview" tab keeps its name because it means a
  preview that updates as you type, not a storefront state.
- **Catalogue cards from `lg` down** (was `md`): at 768 the expanded sidebar
  leaves too little width for a six-column table with its action reachable.
- **Status badges do not animate** between colours; the skip link's target
  (`main#main-content`) is focusable; the editor's section-strip chevrons are
  named.
- **Test hardening**: the two known timing-sensitive assertions
  (`publish-integrity` reading `page.url()` synchronously after
  `router.replace`; `catalogue` expecting an error state inside the query
  client's retry backoff) wait for the state instead of racing it.
- **Dead code removed** after checking references: the L2A-era
  `ProductMetrics`, `ProductIdentity`, `ProductEditorBreadcrumb`,
  `SupplierSyncStatus` components, `estimateMarginPercent`, `useMounted`.

`docs/ux/ux-l2d-phase-2-status.md` records the integration record (merged
into `develop` 2026-09-17 via PR #9), the state at implementation freeze and
the backend dependencies that remain open.

## Known limitations

1. **Global search and the help centre do not exist yet.** Until UX-L2D-02
   they rendered as permanently disabled top-bar controls; two dead buttons in
   the most prominent bar read as broken rather than unfinished, so they are
   gone until the features arrive. Search needs an index of products and
   orders that does not exist; help has no content.

2. **The notification centre has no source.** The panel and store are real and
   genuinely empty. A fake unread badge would train users to ignore the badge.

3. **Reports revenue has no currency.** `GET /analytics/dashboard` sums
   `Order.total_amount` across orders whose `currency` differs, with no FX
   and no currency field in the payload; `/analytics` still labels it `USD`.
   Home shows no monetary figure for that reason. Correcting Reports needs a
   backend contract (per-currency totals or a converted figure).

4. **`/settings` has no editable fields.** The account details it would show are
   already in the user menu; a read-only form that cannot save would look
   broken.

5. **The dashboard bundle is 108 kB** against ~192 B for other routes, almost
   entirely Recharts. Acceptable for one route behind authentication; if
   analytics adds more chart-heavy pages, consider dynamic imports so the
   library loads only where used.

6. **Route protection is client-side.** `middleware.ts` cannot see the httpOnly,
   path-scoped session cookie, so `AuthGuard` performs the check. Bypassing it
   reveals an empty shell — the API is the real boundary.

7. **Tests need a running backend.** The shell suite registers real accounts
   through the API rather than stubbing the network, so it skips when the
   backend is unreachable. A mocked session would keep passing after a genuine
   regression in token handling.
