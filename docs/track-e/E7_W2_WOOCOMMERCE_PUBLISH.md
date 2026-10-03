# Track E7 W2 — publish a draft to WooCommerce

Status: **implemented; verified against a faked WooCommerce API only.**

## What it does

In **Review & publish**, a connected WooCommerce store appears next to the
Shopify and eBay stores. Choosing it sends readiness and publish to
`/integrations/woocommerce/publish-readiness` and `/publish`, which are
admin-only like the other channels.

The draft becomes a **simple** WooCommerce product:

| Field | Value |
|---|---|
| Name | the draft's title |
| Description | the draft's description |
| `regular_price` | the merchant's selling price |
| Stock | managed, set to the draft's quantity |
| `sku` | `dp-<product id>` |
| Images | sent by URL on the first publish only |

## Readiness rules (`woocommerce` channel)

- **The store must be connected through the verified W1 path:** keys
  present, plus the currency stamp that only the verified connect writes.
  A store whose keys came from the generic store endpoint is refused.
- **One variant.** Variable products are a later stage.
- **A selling price** in the store's verified currency. As with eBay, it is
  never the supplier's list price.
- **At least one item in stock.**
- **A title.**
- **Destination:** the same check as the other channels.
- **No image requirement.** WooCommerce accepts a product without one.

The content helpers (`offer_terms`, `listing_text`, `image_urls`) are the
ones eBay uses, imported from `app/integrations/ebay/listing_content.py`
rather than copied. Their rules are the same, and one copy keeps readiness
and publish agreeing.

## Never two products for one draft

Before creating a product, the publish looks for an existing one in this
order:

1. The product id recorded on the listing. A product that has since been
   deleted or trashed in WordPress is skipped.
2. A store search by SKU.

A retry after a lost response therefore adopts the product instead of
creating a second one.

## Known limitations

- **Images are sent on create only.** WooCommerce downloads every image URL
  into the media library on each save, so re-sending them would fill the
  merchant's library with copies. Image changes after the first publish are
  made in WordPress for now.
- **Simple products only.** A draft with more than one enabled variant is
  blocked with `woocommerce_multiple_variants`.
- **Write permission is not checked in advance.** A read-only key fails at
  the first publish, and the store's own error message is shown.
- **Price and stock do not follow automatically yet.** That is W3.

## Verified

`tests/integration/test_woocommerce_publish.py` uses real Postgres and an
in-memory fake store. It covers:

- create and record;
- an update that does not resend images;
- adoption by SKU after a lost response;
- recreating a product that was deleted in WordPress;
- readiness blocking a wrong currency and zero stock before any write;
- the store's refusal reaching the merchant;
- disconnected and unverified stores being refused;
- members being refused.

`frontend/tests/e2e/woocommerce-publish.spec.ts` checks that choosing a
WooCommerce store routes readiness and publish to the WooCommerce endpoints
and never to Shopify.

## Not verified

A real WooCommerce store. This is an owner item: connect a test site and
publish one simple product.
