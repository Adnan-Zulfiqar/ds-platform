"use client";

import { AlertCircle, ImageIcon, Loader2 } from "lucide-react";
import { useState } from "react";

import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

interface ProductThumbnailProps {
  url: string | null | undefined;
  title: string;
  loading?: boolean;
  className?: string;
  /** Opens Images & video when the merchant activates the control. */
  onOpenMedia?: () => void;
}

/**
 * Featured product image for the editor command bar.
 *
 * Never shows a browser broken-image icon. Failed loads fall back to a calm
 * placeholder with accessible text — the merchant’s chosen URL is not replaced.
 */
export function ProductThumbnail({
  url,
  title,
  loading = false,
  className,
  onOpenMedia,
}: ProductThumbnailProps) {
  const [failed, setFailed] = useState(false);
  const size =
    "h-12 w-12 shrink-0 rounded-[10px] md:h-14 md:w-14";

  if (loading) {
    return (
      <Skeleton
        className={cn(size, className)}
        aria-hidden="true"
        data-testid="product-editor-thumbnail-loading"
      />
    );
  }

  const shellClass = cn(
    size,
    "border border-border/80 bg-muted/40",
    onOpenMedia &&
      "cursor-pointer transition-colors hover:border-primary/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
    className,
  );

  const openMedia = () => onOpenMedia?.();

  if (url && !failed) {
    const image = (
      // eslint-disable-next-line @next/next/no-img-element -- remote supplier CDN URLs vary by host
      <img
        src={url}
        alt={title ? `Product image for ${title}` : "Product image"}
        className={cn(size, "border border-border/80 object-cover", className)}
        data-testid="product-editor-thumbnail"
        onError={() => setFailed(true)}
      />
    );
    if (!onOpenMedia) return image;
    return (
      <button
        type="button"
        className={cn(shellClass, "overflow-hidden p-0")}
        onClick={openMedia}
        aria-label="Open Images and video"
      >
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src={url}
          alt=""
          className="h-full w-full object-cover"
          data-testid="product-editor-thumbnail"
          onError={() => setFailed(true)}
        />
        <span className="sr-only">
          {title ? `Product image for ${title}` : "Product image"}
        </span>
      </button>
    );
  }

  const PlaceholderIcon = failed ? AlertCircle : ImageIcon;
  const label = failed
    ? "Product image unavailable"
    : `${title || "Product"} — no image yet`;

  const body = (
    <div
      className={cn(
        shellClass,
        "flex flex-col items-center justify-center gap-0.5 text-muted-foreground",
      )}
      role={onOpenMedia ? undefined : "img"}
      aria-label={onOpenMedia ? undefined : label}
      title={label}
      data-testid={
        failed
          ? "product-editor-thumbnail-error"
          : "product-editor-thumbnail-placeholder"
      }
    >
      <PlaceholderIcon className="h-5 w-5" aria-hidden="true" />
      <span className="sr-only">{label}</span>
    </div>
  );

  if (!onOpenMedia) return body;

  return (
    <button
      type="button"
      className="rounded-[10px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      onClick={openMedia}
      aria-label={`${label}. Open Images and video`}
    >
      {body}
    </button>
  );
}

/** Tiny spinner used while a remote image is still resolving (optional). */
export function ProductThumbnailSpinner({ className }: { className?: string }) {
  return (
    <div
      className={cn(
        "flex h-12 w-12 items-center justify-center rounded-[10px] border border-border/80 bg-muted/40 md:h-14 md:w-14",
        className,
      )}
      aria-hidden="true"
    >
      <Loader2 className="h-4 w-4 animate-spin text-muted-foreground motion-reduce:animate-none" />
    </div>
  );
}
