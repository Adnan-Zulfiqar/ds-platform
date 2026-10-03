# Whiteto local stack via Cloudflare Tunnel

How this machine runs DropPilot so that AliExpress, Shopify and eBay OAuth
callbacks and webhooks reach **local Docker** through
`https://api.whiteto.com`, while you browse the UI on `http://localhost`
(or `:3000`).

## Intended hostnames

| Hostname | Should reach | Observed 2026-10-03 |
|---|---|---|
| `api.whiteto.com` | this PC's `droppilot` backend on `:8000` | **Not this PC.** Only provider callback and deletion paths are routed, and they reach a DropPilot backend on another host |
| `app.whiteto.com` | local frontend on `:3000` | DNS not configured |
| Browser UI | `http://localhost` (nginx) or `:3000` | Working |

OAuth **callbacks** stay on `api.whiteto.com`, which is what each provider
console has registered. After a callback, DropPilot sends the browser to
`*_FRONTEND_RETURN_URL`, currently
`http://localhost:3000/settings/integrations`.

## Diagnosis 2026-10-03: AliExpress stuck on "Awaiting authorization"

**Symptom.** Connect leaves `aliexpress_connections.status = pending` with no
token. The local logs show `aliexpress_connection_started` but never a
`GET /api/v1/integrations/aliexpress/callback`.

**What was proven:**

1. **No tunnel connector runs on this PC.** There is no `cloudflared`
   process, Windows service, WSL distro (only `docker-desktop`) or container.
2. **Ingress is path-scoped.** `https://api.whiteto.com/health`,
   `/api/v1/auth/me` and `/docs` return Cloudflare's own 404 with no
   `x-request-id`. Only the provider paths (`…/aliexpress/callback`,
   `…/shopify/callback`, `…/ebay/marketplace-account-deletion`) reach a
   backend.
3. **That backend is not this one.** A public callback returns a genuine
   DropPilot response (303 to `localhost:3000/...?aliexpress=failed`, with an
   `x-request-id`), but that request id appears in **no** container log on
   this machine. A local request with the same URL logs normally.

**Root cause.** The `api.whiteto.com` tunnel connector runs on another host,
whose `localhost:8000` is a different DropPilot backend. AliExpress returns
the user there. That backend's Redis has no matching OAuth `state`, so the
callback fails there, and this PC's row stays `pending`. Earlier notes said
the probe "reaches this Docker backend". The 303 looks identical either way;
correlating the request id proved otherwise.

## Fix: owner checklist (Cloudflare dashboard; the agent cannot do this)

The connector needs the tunnel token, a secret that only the dashboard
shows. Do not paste it in chat.

1. Cloudflare Zero Trust → **Networks → Tunnels** → open the tunnel that
   serves `api.whiteto.com`. Under **Connectors**, note which machine is
   connected.
2. **Stop the other machine's connector** for this tunnel (or move
   `api.whiteto.com` to a new tunnel). Two connectors on one tunnel share
   traffic, so callbacks would land on either machine.
3. On **this PC**, in an administrator PowerShell, run the install command
   the dashboard shows for Windows (`cloudflared.exe service install
   <token>`). It installs `cloudflared` as a Windows service.
4. **Public Hostname** for `api.whiteto.com`: keep **one** rule with path
   `*` (or empty) → service `http://localhost:8000`. Remove the path-scoped
   callback rules.
5. Tell the agent "tunnel done". The checks below are then run again.

Optionally, later: `app.whiteto.com`, path `*` → `http://localhost:3000`,
then switch `*_FRONTEND_RETURN_URL`, `CORS_ORIGINS` and
`NEXT_PUBLIC_API_URL` to the public hostnames.

## Verify that the public hostname reaches THIS backend

A 303 alone proves nothing, because any DropPilot backend answers the same
way. Correlate the request id instead:

```bash
rid=$(curl -s -D - -o /dev/null "https://api.whiteto.com/health" | grep -i '^x-request-id' | cut -d' ' -f2 | tr -d '\r')
echo "public request id: ${rid:-none (Cloudflare 404: no ingress rule)}"
docker logs --since 2m droppilot-backend-1 2>&1 | grep -c "$rid"   # must be >= 1
```

The expected results after the fix:

- `https://api.whiteto.com/health` → **200**, with a request id that appears
  in `droppilot-backend-1` logs.
- `https://api.whiteto.com/api/v1/integrations/aliexpress/callback?code=x&state=y`
  → 303 to `…?aliexpress=expired`, also logged locally.

## Then reconnect AliExpress

1. Integrations → AliExpress → **Connect**. Each Connect issues a fresh
   OAuth state, so stale `pending` rows do not block it. The two stale rows
   were cleared on 2026-10-03.
2. Finish consent in the same browser. The AliExpress app is in `test`
   status, so the AliExpress account must be on the app's test allow-list.
3. Expect **Connected**. If the page says "Authorization not recognised"
   (`?aliexpress=expired`), the callback reached a server that did not issue
   the state, so the tunnel is still pointing elsewhere.

## Local `.env` notes (this checkout)

- `ALIEXPRESS_CALLBACK_URL=https://api.whiteto.com/api/v1/integrations/aliexpress/callback`
  is defined once, and its value matches what the backend sends as
  `redirect_uri`.
- `ALIEXPRESS_APP_KEY` and `ALIEXPRESS_APP_SECRET` are each defined
  **twice**. The first occurrences (around lines 220–221) are empty. The
  later ones hold the real values and win, and the running container uses
  them. **Delete the empty first pair** so a reorder can never blank the
  credentials.
- `ALIEXPRESS_CATALOG_ACCESS_TOKEN` is **missing**. Product import (link →
  draft) needs it unless every workspace connects its own AliExpress
  account. Paste the platform dropshipper access token into `.env` (never
  into chat), then recreate the containers:

```powershell
docker compose -p droppilot --env-file .env up -d --no-build backend worker beat
```

## Provider consoles (must match exactly)

| Provider | Public URL |
|---|---|
| AliExpress callback | `https://api.whiteto.com/api/v1/integrations/aliexpress/callback` |
| Shopify callback | `https://api.whiteto.com/api/v1/integrations/shopify/callback` |
| Shopify webhook base | `https://api.whiteto.com/api/v1/integrations/shopify/webhook` |
| eBay marketplace deletion | `https://api.whiteto.com/api/v1/integrations/ebay/marketplace-account-deletion` |
