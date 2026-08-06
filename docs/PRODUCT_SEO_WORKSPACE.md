# Product SEO workspace

## Editable Shopify fields

- SEO title → `metafields_global_title_tag` on publish
- Meta description → `metafields_global_description_tag`
- URL handle (`slug`) → preferred Shopify handle when creating
- Shopify product tags → `tags` string on publish
- Redirect-old-handle flag stored locally (Shopify redirects when supported)

## Planning-only fields (not HTML meta keywords)

- `search_topics` — Supporting search topics
- `seo_planning` — intent, primary topic, etc.

**Meta keywords are never exported.** The legacy `meta_keywords` column remains
for compatibility but is not shown as a Google ranking field and is not sent to
Shopify.

## Score (0–100)

Transparent sections: intent, title, description, uniqueness, content, images,
URL, structured-data readiness, indexability, compliance. Statuses: Poor /
Needs Work / Good / Excellent. Advisory only — no ranking guarantees.
