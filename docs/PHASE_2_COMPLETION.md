# Phase 2 — Completion Report

**Project:** DropPilot AI — multi-tenant dropshipping automation platform
**Phase:** 2 — Application shell, dashboard layout, navigation, SaaS UI foundation
**Completed:** 2026-07-31
**Feature commit:** `1839869`
**Branch:** `develop`

Phase 2 delivered the interface foundation every future module renders into. It
contains **no business functionality** — no product import, no store
connections, no orders. Authentication was not modified.

---

## 1. Architecture decisions

### One navigation manifest

`lib/navigation.ts` is the single source of truth. The desktop sidebar, the
mobile drawer, and the top bar's current-page label all read from it.

Phase 1 kept the list inside the sidebar component, reachable by exactly one
consumer. Promoting it to data means a route cannot exist in one surface and be
missing from another, and a future command palette or breadcrumb trail gets it
for free.

### `status` keeps navigation honest

Each entry declares `ready` or `coming-soon`. Unbuilt destinations render as
**non-interactive `div`s**, not disabled links or buttons.

That distinction matters: a disabled interactive element is still reachable by
keyboard and then does nothing when activated, which is more confusing than an
element that was never interactive. `aria-disabled` communicates the state
without the dead stop.

The rule this enforces — **primary navigation must never reach a 404** — is why
five routes exist with placeholder content rather than being omitted.

### Desktop and mobile split in CSS, not JavaScript

The sidebar is `hidden md:flex`; the drawer trigger is `md:hidden`. Both are
present in server-rendered HTML, so the correct one appears before hydration. A
JavaScript viewport check would render the wrong navigation for a frame.

`useIsDesktop` exists only for behaviour CSS cannot express — closing the drawer
when the viewport grows past the breakpoint.

### Three component tiers

| Tier | Location | May know about |
|---|---|---|
| Primitives | `components/ui/` | Nothing — no API types, routes, or stores |
| Composed | `components/navigation/`, `components/dashboard/` | Routes, session, stores |
| Layouts | `layouts/` | How the pieces fit together |

A primitive importing `useAuth` has left its tier. This is what keeps
`components/ui/` genuinely reusable rather than quietly coupled to this
application.

### Scrolling belongs to `main`, not the document

The sidebar and top bar stay put without `position: fixed`, which avoids content
sliding underneath and avoids mobile browsers mismeasuring the viewport as their
address bar collapses.

### Charts read design tokens at runtime

Recharts renders SVG and takes colours as props, so Tailwind classes cannot
style the marks. `chart-theme.ts` passes `hsl(var(--primary))` into `fill` and
`stroke` instead. A theme switch therefore recolours every chart with no
JavaScript — the CSS variables themselves change.

### Empty, error, and loading are three separate components

Conflating them produces the worst failure a dashboard has: a request fails and
the user is told they have no products. `Skeleton`, `EmptyState`, and
`ErrorState` are distinct, and `ChartContainer` owns all four states so no
individual chart implements only the happy path.

### Mock data is quarantined, and the UI says so

Every dashboard figure is invented. The module lives under `lib/mock/`, every
export is prefixed `MOCK_`, nothing outside the dashboard imports it, and the
page carries a visible banner. A dashboard that looks authoritative and is not
is worse than an empty one.

---

## 2. Frontend structure

```
frontend/
├── app/
│   ├── (auth)/              login, register, forgot-password
│   ├── (protected)/         AuthGuard + AppShell
│   │   ├── dashboard/       built
│   │   ├── products/ stores/ orders/ analytics/ settings/
│   │   ├── loading.tsx  error.tsx
│   │   └── layout.tsx
│   └── unauthorized/        403 — deliberately outside (protected)
├── layouts/app-shell.tsx    sidebar + top bar + scrolling main
├── components/
│   ├── ui/                  19 primitives
│   ├── navigation/          7 components
│   └── dashboard/           stat card, chart frame, 3 charts
├── features/                reserved — empty on purpose
├── hooks/                   use-media-query, use-mounted
├── lib/
│   ├── navigation.ts        the manifest
│   └── mock/                quarantined placeholder data
├── services/                query keys and types
├── stores/                  ui-store, notification-store
└── tests/e2e/               Playwright
```

`features/` is deliberately empty: creating a directory structure before there
is code to put in it is guesswork about a shape that has not been designed.

---

## 3. Components created

**Navigation (7)** — `sidebar`, `sidebar-nav`, `nav-item`, `mobile-nav`,
`top-nav`, `user-menu`, `notification-menu`

**Dashboard (5)** — `stat-card`, `chart-container`, `sales-chart`,
`orders-chart`, `product-performance-chart`, plus the shared `chart-theme`

**Design system (8 new)** — `avatar`, `tooltip`, `sheet`, `separator`,
`empty-state`, `error-state`, `page-header`, `coming-soon`

**Other** — `app-shell`, `use-media-query`, `use-mounted`, `navigation.ts`,
`notification-store`, three service modules

Two components were removed: `layouts/sidebar.tsx` and `layouts/top-nav.tsx`,
split into focused components under `components/navigation/`.

---

## 4. Routes created

| Route | Status |
|---|---|
| `/dashboard` | Built — stat cards, charts, sample-data banner |
| `/products` | Placeholder |
| `/stores` | Placeholder |
| `/orders` | Placeholder |
| `/analytics` | Placeholder |
| `/settings` | Placeholder |
| `/unauthorized` | Built — 403 |
| `(protected)/loading` | Skeleton mirroring the dashboard's proportions |
| `(protected)/error` | Scoped boundary, keeps the shell intact |

**Four navigation entries have no route** — Import Product, Suppliers,
Customers, Automation. They render as `coming-soon` items rather than linking
anywhere.

---

## 5. Testing results

All executed on this machine.

| Check | Result |
|---|---|
| ruff | All checks passed |
| ruff format | 77 files already formatted |
| mypy (strict) | No issues, 62 files |
| pytest | **167 passed** |
| eslint | Clean |
| tsc | Clean |
| next build | Compiled successfully, 14 routes |
| **Playwright** | **47 passed** |

The Playwright suite runs against a **real backend with a real database**. It
registers accounts through the API rather than stubbing the network — a mocked
session would keep passing after a genuine regression in token handling, cookie
attributes, or the shape of `/auth/me`.

Coverage spans sidebar rendering and collapse persistence, mobile drawer
behaviour, theme switching and persistence, protected-route access, dashboard
rendering, and the user menu — at **320px, 768px, and 1440px** in both themes,
including an explicit assertion that the document does not scroll horizontally
at 320px.

### Defects found by running the application

1. **`CORS_ORIGINS` could not be set in its documented format.** A latent
   Phase 0 defect. pydantic-settings runs `json.loads` on list-typed fields
   before validators execute, so the comma-separated form in `.env.example`
   raised `JSONDecodeError` during boot — the backend could not start with its
   own example configuration. Found while starting the server for these tests.
   Fixed with `NoDecode` plus a validator accepting both forms, and covered by
   six regression tests.

2. **The user menu showed the email address twice** for accounts with no name
   set, because `displayName` falls back to the email. Found by a test written
   strictly enough to catch it; the component was fixed rather than the test
   loosened.

---

## 6. Known limitations

1. **Every dashboard figure is mock data.** Contained and declared — see the
   quarantine described above.

2. **Global search and the help centre are non-functional.** Both render as
   disabled controls with explanatory tooltips. Search needs an index of
   products and orders that do not exist; help has no content.

3. **The notification centre has no source.** The panel and store are real and
   genuinely empty. A fake unread badge would train users to ignore the badge —
   the wrong habit to build into a product whose value later depends on alerting
   people to failed order syncs.

4. **`/settings` has no editable fields.** The account details it would show are
   already in the user menu; a read-only form that cannot save would look broken.

5. **The dashboard bundle is 108 kB** against ~192 B for other routes, almost
   entirely Recharts.

6. **Route protection is client-side.** `middleware.ts` cannot see the httpOnly,
   path-scoped session cookie, so `AuthGuard` performs the check. Bypassing it
   reveals an empty shell — the API is the real boundary.

7. **The Playwright suite needs a running backend** and skips cleanly without
   one.

8. **Docker remains unbuilt** — unchanged from Phase 1, and unchangeable on this
   machine. Still the only critical debt item.

---

## 7. Future improvements

Ranked by value, none blocking.

| Improvement | Trigger |
|---|---|
| Replace mock data with real endpoints | The first analytics endpoint |
| Dynamic-import the charts | A second chart-heavy route |
| Generate API types from OpenAPI | Before the API surface grows |
| Global search | An index of products and orders exists |
| Real notification source | Background jobs that can fail |
| Settings forms | The profile and team endpoints |
| Command palette | Reads the existing navigation manifest, so it is cheap |
| Breadcrumbs for nested routes | The first route more than one level deep |

---

## 8. Deviations from the phase specification

One, recorded so it is deliberate rather than accidental.

**Service modules are named `services/dashboard.ts`, not
`dashboard.service.ts`.** The brief used the latter as an example; Phase 1 had
already established the former, and CLAUDE.md requires extending existing
conventions before creating new ones. Consistency across the directory was
judged worth more than matching an illustration.

Everything else — the layout, sidebar sections, top navigation contents,
dashboard cards, chart set, design-system components, theme support, responsive
targets, routing, state management, API preparation, and testing — follows the
specification as written.
