# Shopify publication result

## Persisted on `StoreListing` (migration `0018`)

- `external_product_id` (REST numeric)
- `external_graphql_id` (`gid://shopify/Product/{id}`)
- `external_handle` (from Shopify response)
- `shop_domain`, `admin_url`
- `storefront_url` — only when Shopify `status=active` and `published_at` present
- `online_store_published` — true/false/null (null = unverified)
- sync timestamps / errors

## UI actions

| Button | When shown |
|---|---|
| View in Store | Verified `storefront_url` and not explicitly unpublished |
| Manage in Shopify | Verified `admin_url` |
| Copy Product URL | When storefront URL exists |

Broken storefront buttons are never shown for unverified URLs.

## Publications API / `write_publications`

Not required for create/update product + soft storefront URL from REST
`published_at`. True Online Store channel publish/unpublish needs publications
API + `write_publications` and merchant reauthorization — documented as debt
(M24), not silently claimed as complete.
