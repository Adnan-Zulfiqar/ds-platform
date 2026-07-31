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

---

## Navigation

`lib/navigation.ts` is the single source of truth. The desktop sidebar, the
mobile drawer, and the top bar's current-page label all read from it.

Each item carries a `status`:

* `ready` — renders as a link.
* `coming-soon` — renders as a non-interactive `div` with a "Soon" badge and
  `aria-disabled`.

A `coming-soon` item is **not** a disabled link or button. A disabled
interactive element is still reachable by keyboard and then does nothing, which
is more confusing than an element that was never interactive.

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

## Mock data

`lib/mock/dashboard-data.ts` is quarantined: nothing outside the dashboard
imports it, and every export is prefixed `MOCK_`.

**The dashboard renders a visible banner stating the figures are placeholders.**
A dashboard that looks authoritative and is not is worse than an empty one.

Migration when the endpoint lands: add fetchers to `services/dashboard.ts`, swap
the imports in the dashboard page, delete the mock module. A surviving `MOCK_`
reference anywhere means the migration is incomplete — which is the point of the
naming.

---

## Known limitations

1. **Global search and the help centre are non-functional.** Both render as
   disabled controls with explanatory tooltips. Search needs an index of
   products and orders that do not exist; help has no content. Visible so the
   layout is final, disabled so they are honest.

2. **The notification centre has no source.** The panel and store are real and
   genuinely empty. A fake unread badge would train users to ignore the badge.

3. **Every dashboard figure is mock data.** See above.

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
