"use client";

import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { useDraftSeoScore } from "@/services/drafts";

interface DraftSeoPanelProps {
  productId: string;
  productTitle: string;
  seoTitle: string;
  seoDescription: string;
  slug: string;
  tags: string;
  searchTopics: string;
  primaryIntent: string;
  primaryTopic: string;
  redirectOldHandle: boolean;
  ogTitle: string;
  ogDescription: string;
  onChange: (patch: {
    seoTitle?: string;
    seoDescription?: string;
    slug?: string;
    tags?: string;
    searchTopics?: string;
    primaryIntent?: string;
    primaryTopic?: string;
    redirectOldHandle?: boolean;
    ogTitle?: string;
    ogDescription?: string;
  }) => void;
}

function SerpPreview({
  title,
  description,
  slug,
}: {
  title: string;
  description: string;
  slug: string;
}) {
  const displayTitle = title || "Your SEO title appears here";
  const displayDesc =
    description || "Your meta description appears here in search results.";
  const path = slug ? `/products/${slug}` : "/products/your-handle";

  return (
    <div className="rounded-lg border bg-muted/30 p-4">
      <p className="text-xs text-muted-foreground">Search result preview</p>
      <p className="mt-2 truncate text-sm text-emerald-700 dark:text-emerald-400">
        store.example.com{path}
      </p>
      <p className="mt-1 text-lg font-medium text-blue-700 dark:text-blue-400">
        {displayTitle}
      </p>
      <p className="mt-1 line-clamp-2 text-sm text-muted-foreground">
        {displayDesc}
      </p>
      <p className="mt-3 text-xs text-muted-foreground">
        Search engines may rewrite snippets. This preview is advisory only.
      </p>
    </div>
  );
}

export function DraftSeoPanel({
  productId,
  productTitle,
  seoTitle,
  seoDescription,
  slug,
  tags,
  searchTopics,
  primaryIntent,
  primaryTopic,
  redirectOldHandle,
  ogTitle,
  ogDescription,
  onChange,
}: DraftSeoPanelProps) {
  const seoScore = useDraftSeoScore(productId);
  const titleLen = seoTitle.trim().length;
  const descLen = seoDescription.trim().length;

  return (
    <section className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold">SEO workspace</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Expert ecommerce SEO assistant — proposals and scores are advisory,
            not ranking guarantees. Search topics are planning inputs only.
          </p>
        </div>
        {seoScore.data ? (
          <div className="rounded-lg border px-4 py-2 text-center">
            <p className="text-3xl font-semibold tabular-nums">
              {seoScore.data.score}
            </p>
            <Badge variant="outline">{seoScore.data.status}</Badge>
          </div>
        ) : null}
      </div>

      <SerpPreview
        title={seoTitle || productTitle}
        description={seoDescription}
        slug={slug}
      />

      <div className="grid gap-4 lg:grid-cols-2">
        <div className="space-y-2">
          <Label htmlFor="seo-title">SEO title</Label>
          <Input
            id="seo-title"
            value={seoTitle}
            onChange={(e) => onChange({ seoTitle: e.target.value })}
          />
          <p className="text-xs text-muted-foreground">
            {titleLen} characters · recommended ~30–65 (visual guide, not a hard
            limit)
          </p>
          {seoTitle &&
          productTitle &&
          seoTitle.trim().toLowerCase() === productTitle.trim().toLowerCase() ? (
            <p className="text-xs text-amber-700 dark:text-amber-400">
              Matches product title — consider a customer-focused rewrite.
            </p>
          ) : null}
        </div>
        <div className="space-y-2">
          <Label htmlFor="seo-slug">URL handle</Label>
          <Input
            id="seo-slug"
            value={slug}
            onChange={(e) => onChange({ slug: e.target.value })}
          />
          <label className="flex items-center gap-2 text-xs text-muted-foreground">
            <input
              type="checkbox"
              checked={redirectOldHandle}
              onChange={(e) =>
                onChange({ redirectOldHandle: e.target.checked })
              }
            />
            Redirect old handle when changed (Shopify-side when supported)
          </label>
        </div>
      </div>

      <div className="space-y-2">
        <Label htmlFor="seo-desc">Meta description</Label>
        <Textarea
          id="seo-desc"
          rows={4}
          value={seoDescription}
          onChange={(e) => onChange({ seoDescription: e.target.value })}
        />
        <p className="text-xs text-muted-foreground">
          {descLen} characters · recommended ~70–165
        </p>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <div className="space-y-2">
          <Label htmlFor="shopify-tags">Shopify product tags</Label>
          <Input
            id="shopify-tags"
            value={tags}
            onChange={(e) => onChange({ tags: e.target.value })}
            placeholder="summer, usb-hub, travel"
          />
          <p className="text-xs text-muted-foreground">
            Used for collections, filtering, and merchandising — not HTML meta
            keywords.
          </p>
        </div>
        <div className="space-y-2">
          <Label htmlFor="search-topics">Supporting search topics</Label>
          <Input
            id="search-topics"
            value={searchTopics}
            onChange={(e) => onChange({ searchTopics: e.target.value })}
            placeholder="usb-c hub, laptop adapter"
          />
          <p className="text-xs text-muted-foreground">
            DropPilot planning context for AI/content. Never exported as a
            meta-keywords tag.
          </p>
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <div className="space-y-2">
          <Label htmlFor="primary-intent">Primary search intent</Label>
          <select
            id="primary-intent"
            className="flex h-9 w-full rounded-md border bg-background px-2 text-sm"
            value={primaryIntent}
            onChange={(e) => onChange({ primaryIntent: e.target.value })}
          >
            <option value="">Select intent…</option>
            <option value="transactional">Transactional</option>
            <option value="commercial">Commercial investigation</option>
            <option value="informational">Informational</option>
          </select>
        </div>
        <div className="space-y-2">
          <Label htmlFor="primary-topic">Primary topic</Label>
          <Input
            id="primary-topic"
            value={primaryTopic}
            onChange={(e) => onChange({ primaryTopic: e.target.value })}
          />
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <div className="space-y-2">
          <Label htmlFor="og-title">Open Graph title</Label>
          <Input
            id="og-title"
            value={ogTitle}
            onChange={(e) => onChange({ ogTitle: e.target.value })}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="og-desc">Open Graph description</Label>
          <Input
            id="og-desc"
            value={ogDescription}
            onChange={(e) => onChange({ ogDescription: e.target.value })}
          />
        </div>
      </div>

      {seoScore.data ? (
        <div className="rounded-lg border p-4">
          <h3 className="text-sm font-semibold">Score breakdown</h3>
          <ul className="mt-2 grid gap-1 text-xs text-muted-foreground sm:grid-cols-2">
            {Object.entries(seoScore.data.sections).map(([key, value]) => (
              <li key={key}>
                {key.replaceAll("_", " ")}: {value}
              </li>
            ))}
          </ul>
          {seoScore.data.warnings.length > 0 ? (
            <ul className="mt-3 space-y-1 text-sm text-amber-700 dark:text-amber-400">
              {seoScore.data.warnings.map((warning) => (
                <li key={warning}>• {warning}</li>
              ))}
            </ul>
          ) : null}
          <p className="mt-3 text-xs text-muted-foreground">{seoScore.data.note}</p>
        </div>
      ) : null}
    </section>
  );
}
