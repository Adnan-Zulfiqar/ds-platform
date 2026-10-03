# Track E7 W4b — live WooCommerce orders by webhook

Status: **implemented; verified against a faked WooCommerce API only.**

## What it does

- **On connect,** when `WOOCOMMERCE_WEBHOOK_CALLBACK_BASE` is https,
  DropPilot registers `order.created` and `order.updated` webhooks on the
  store. Their delivery URL is `<base>/<tenant id>/<store id>`.
  - Each store gets its own signing secret, kept with its encrypted keys.
  - The webhook ids are recorded in `stores.settings.woocommerceWebhookIds`.
- **On delivery,** the HMAC-SHA256 signature (`X-WC-Webhook-Signature`) is
  checked against that store's secret. The order is then **re-fetched** from
  the store with `GET /orders/{id}` and upserted, exactly as the W4a import
  does.
- **WooCommerce's unsigned creation ping** (`webhook_id=…`) is acknowledged
  and does nothing.
- **On reconnect or disconnect,** the old webhooks are deleted, best effort.

## Decisions (D-014)

- **Tenant in the URL.** The tenant only narrows a scoped lookup, and the
  signature authorises the delivery. A forged or swapped tenant, an unknown
  store, a missing signature and a wrong signature all get the same 401.
  There is no unscoped request-path query.
- **The payload is a doorbell.** Its contents are never written. A replayed
  or out-of-order delivery cannot overwrite newer state, so there is no
  replay cache to fail open or closed. The cost is one extra API call per
  delivery.
- **https only.** In development the base is `http://localhost…`, so nothing
  is registered and orders come in through "Import recent orders" instead.
- **Best-effort registration.** A store that refuses webhooks (an older
  WooCommerce, or a key without permission) still connects. Its
  `last_error` explains this and points to the manual import.

## Configuration

`WOOCOMMERCE_WEBHOOK_CALLBACK_BASE` must be the public https address of
`/api/v1/integrations/woocommerce/webhooks`. It is in `.env.example` and the
Lightsail example.

## Verified

`tests/integration/test_woocommerce_webhooks.py` covers:

- registration at a URL naming the tenant and store, with that store's
  secret;
- a signed delivery re-fetching the order, so the store's state wins over
  the payload;
- a wrong signature, a missing signature, a forged tenant and an unknown
  store all getting 401 and writing nothing;
- the ping being acknowledged;
- reconnect and disconnect removing the old webhooks;
- no registration without an https base;
- a store that refuses webhooks still connecting.

`tests/unit/test_woocommerce_webhook_signature.py` covers the signature
check itself.

## Not verified

A real store delivering to a public deployment (owner item, once deployed).
