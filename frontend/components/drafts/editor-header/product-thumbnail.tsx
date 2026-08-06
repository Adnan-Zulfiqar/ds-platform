import { ImageIcon } from "lucide-react";

import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

interface ProductThumbnailProps {
  url: string | null | undefined;
  title: string;
  loading?: boolean;
  className?: string;
}

/**
 * Featured product image for the editor identity row.
 *
 * Never falls back to user initials — that pattern belongs to avatars, not
 * catalogue thumbnails.
 */
export function ProductThumbnail({
  url,
  title,
  loading = false,
  className,
}: ProductThumbnailProps) {
  if (loading) {
    return (
      <Skeleton
        className={cn("h-16 w-16 shrink-0 rounded-lg md:h-[72px] md:w-[72px]", className)}
        aria-hidden="true"
      />
    );
  }

  if (url) {
    return (
      // eslint-disable-next-line @next/next/no-img-element -- remote supplier CDN URLs vary by host
      <img
        src={url}
        alt=""
        className={cn(
          "h-16 w-16 shrink-0 rounded-lg border border-border/80 object-cover md:h-[72px] md:w-[72px]",
          className,
        )}
        data-testid="product-editor-thumbnail"
      />
    );
  }

  return (
    <div
      className={cn(
        "flex h-16 w-16 shrink-0 items-center justify-center rounded-lg border border-dashed border-border/80 bg-muted/40 text-muted-foreground md:h-[72px] md:w-[72px]",
        className,
      )}
      role="img"
      aria-label={`${title || "Product"} — no image`}
      data-testid="product-editor-thumbnail-placeholder"
    >
      <ImageIcon className="h-6 w-6" aria-hidden="true" />
    </div>
  );
}
