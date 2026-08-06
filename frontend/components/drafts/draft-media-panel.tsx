"use client";

import { useState } from "react";
import { ArrowDown, ArrowUp, Loader2, Star, Trash2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  useAddDraftImage,
  useRemoveDraftImage,
  useReorderDraftImages,
  useUpdateDraftImage,
} from "@/services/drafts";
import type { ProductDetail, ProductImage } from "@/types/api";

interface DraftMediaPanelProps {
  productId: string;
  product: ProductDetail;
}

function duplicateUrls(images: ProductImage[]): Set<string> {
  const seen = new Set<string>();
  const dupes = new Set<string>();
  for (const image of images) {
    if (seen.has(image.url)) dupes.add(image.url);
    seen.add(image.url);
  }
  return dupes;
}

/**
 * Media studio for a draft — reorder, featured, alt text, add-by-URL, remove.
 * File upload / crop land when S3 storage is wired; URLs keep Stage 4 unblocked.
 */
export function DraftMediaPanel({ productId, product }: DraftMediaPanelProps) {
  const images = [...product.images].sort((a, b) => a.position - b.position);
  const dupes = duplicateUrls(images);
  const reorder = useReorderDraftImages(productId);
  const addImage = useAddDraftImage(productId);
  const updateImage = useUpdateDraftImage(productId);
  const removeImage = useRemoveDraftImage(productId);
  const [newUrl, setNewUrl] = useState("");
  const [newAlt, setNewAlt] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function move(imageId: string, direction: -1 | 1) {
    const ids = images.map((image) => image.id);
    const index = ids.indexOf(imageId);
    const target = index + direction;
    if (index < 0 || target < 0 || target >= ids.length) return;
    const next = [...ids];
    const [removed] = next.splice(index, 1);
    if (removed === undefined) return;
    next.splice(target, 0, removed);
    setError(null);
    try {
      await reorder.mutateAsync(next);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Reorder failed.");
    }
  }

  async function makeFeatured(imageId: string) {
    const ids = images.map((image) => image.id).filter((id) => id !== imageId);
    ids.unshift(imageId);
    setError(null);
    try {
      await reorder.mutateAsync(ids);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not set featured image.");
    }
  }

  return (
    <section className="space-y-4" data-testid="draft-media-panel">
      <div>
        <h2 className="text-lg font-semibold">Media</h2>
        <p className="text-sm text-muted-foreground">
          Position 0 is the featured image. Supplier refresh keeps your order and
          alt text; merchant-added URLs are never deleted by sync.
        </p>
      </div>

      {error ? (
        <p className="text-sm text-destructive" role="alert">
          {error}
        </p>
      ) : null}

      {dupes.size > 0 ? (
        <p className="text-sm text-amber-700 dark:text-amber-400">
          Duplicate image URLs detected — consider removing extras.
        </p>
      ) : null}

      <ul className="space-y-3">
        {images.map((image, index) => (
          <li
            key={image.id}
            className="flex flex-col gap-3 rounded-lg border p-3 sm:flex-row sm:items-start"
            data-testid="draft-media-row"
          >
            <div className="relative h-24 w-24 shrink-0 overflow-hidden rounded-md border bg-muted">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={image.url}
                alt={image.altText || `Product image ${index + 1}`}
                className="h-full w-full object-cover"
              />
              {index === 0 ? (
                <Badge className="absolute left-1 top-1" variant="secondary">
                  Featured
                </Badge>
              ) : null}
            </div>
            <div className="min-w-0 flex-1 space-y-2">
              <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                <span>#{image.position}</span>
                <Badge variant="outline">
                  {image.isSupplier ? "Supplier" : "Merchant"}
                </Badge>
                {dupes.has(image.url) ? (
                  <Badge variant="destructive">Duplicate</Badge>
                ) : null}
              </div>
              <p className="truncate text-xs text-muted-foreground">{image.url}</p>
              <div className="space-y-1">
                <Label htmlFor={`alt-${image.id}`}>Alt text</Label>
                <Input
                  id={`alt-${image.id}`}
                  defaultValue={image.altText ?? ""}
                  onBlur={(event) => {
                    const value = event.target.value.trim() || null;
                    if (value === (image.altText ?? null)) return;
                    void updateImage.mutateAsync({
                      imageId: image.id,
                      altText: value,
                    });
                  }}
                />
              </div>
              <div className="flex flex-wrap gap-2">
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={index === 0 || reorder.isPending}
                  onClick={() => void move(image.id, -1)}
                >
                  <ArrowUp className="h-3.5 w-3.5" />
                  Up
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={index === images.length - 1 || reorder.isPending}
                  onClick={() => void move(image.id, 1)}
                >
                  <ArrowDown className="h-3.5 w-3.5" />
                  Down
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={index === 0 || reorder.isPending}
                  onClick={() => void makeFeatured(image.id)}
                >
                  <Star className="h-3.5 w-3.5" />
                  Feature
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="destructive"
                  disabled={removeImage.isPending}
                  onClick={() => void removeImage.mutateAsync(image.id)}
                >
                  <Trash2 className="h-3.5 w-3.5" />
                  Remove
                </Button>
              </div>
            </div>
          </li>
        ))}
      </ul>

      {images.length === 0 ? (
        <p className="text-sm text-muted-foreground">No images on this draft yet.</p>
      ) : null}

      <div className="rounded-lg border p-4 space-y-3">
        <h3 className="text-sm font-semibold">Add merchant image by URL</h3>
        <div className="space-y-2">
          <Label htmlFor="new-image-url">Image URL</Label>
          <Input
            id="new-image-url"
            value={newUrl}
            onChange={(event) => setNewUrl(event.target.value)}
            placeholder="https://…"
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="new-image-alt">Alt text</Label>
          <Input
            id="new-image-alt"
            value={newAlt}
            onChange={(event) => setNewAlt(event.target.value)}
          />
        </div>
        <Button
          type="button"
          disabled={addImage.isPending || !newUrl.trim()}
          onClick={() => {
            setError(null);
            void addImage
              .mutateAsync({
                url: newUrl.trim(),
                altText: newAlt.trim() || null,
              })
              .then(() => {
                setNewUrl("");
                setNewAlt("");
              })
              .catch((err: unknown) => {
                setError(err instanceof Error ? err.message : "Add failed.");
              });
          }}
        >
          {addImage.isPending ? (
            <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
          ) : null}
          Add image
        </Button>
      </div>
    </section>
  );
}
