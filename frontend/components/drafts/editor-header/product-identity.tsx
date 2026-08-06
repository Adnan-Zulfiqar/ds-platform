import { ExternalLink } from "lucide-react";
import type { ReactNode } from "react";

import { ProductThumbnail } from "@/components/drafts/editor-header/product-thumbnail";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

interface ProductIdentityProps {
  title: string;
  externalId: string;
  supplierName: string | null | undefined;
  externalUrl: string | null | undefined;
  thumbnailUrl: string | null | undefined;
  children?: ReactNode;
  className?: string;
}

export function ProductIdentity({
  title,
  externalId,
  supplierName,
  externalUrl,
  thumbnailUrl,
  children,
  className,
}: ProductIdentityProps) {
  return (
    <div
      className={cn("flex min-w-0 flex-1 items-start gap-3", className)}
      data-testid="product-identity"
    >
      <ProductThumbnail url={thumbnailUrl} title={title} />
      <div className="min-w-0 flex-1 space-y-1.5">
        <TooltipProvider delayDuration={300}>
          <Tooltip>
            <TooltipTrigger asChild>
              <h1
                className="line-clamp-1 text-base font-semibold tracking-tight text-foreground sm:text-lg md:line-clamp-2 md:text-xl"
                data-testid="product-editor-title"
              >
                {title || "Untitled draft"}
              </h1>
            </TooltipTrigger>
            <TooltipContent side="bottom" align="start" className="max-w-md">
              {title || "Untitled draft"}
            </TooltipContent>
          </Tooltip>
        </TooltipProvider>
        <span className="sr-only">{title || "Untitled draft"}</span>

        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-muted-foreground">
          <span>
            AliExpress #{externalId}
          </span>
          {supplierName ? (
            <>
              <span aria-hidden="true" className="text-border">
                ·
              </span>
              <span>{supplierName}</span>
            </>
          ) : null}
          {externalUrl ? (
            <a
              href={externalUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center rounded-sm text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              aria-label="Open AliExpress listing in a new tab"
            >
              <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
            </a>
          ) : null}
        </div>

        {children}
      </div>
    </div>
  );
}
