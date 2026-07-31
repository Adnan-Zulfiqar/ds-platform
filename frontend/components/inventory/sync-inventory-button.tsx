"use client";

import { RefreshCw } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { useSyncInventory } from "@/services/inventory";

export function SyncInventoryButton() {
  const sync = useSyncInventory();
  const [message, setMessage] = useState<string | null>(null);

  return (
    <div className="flex items-center gap-3">
      {message && (
        <p className="text-sm text-muted-foreground" role="status">
          {message}
        </p>
      )}
      <Button
        onClick={() => {
          setMessage(null);
          sync.mutate(
            {},
            {
              onSuccess: (run) => {
                setMessage(
                  `Sync ${run.status}: ${run.productsChanged} changed of ${run.productsSeen} seen.`,
                );
              },
              onError: (err) => {
                setMessage(err instanceof Error ? err.message : "Sync failed.");
              },
            },
          );
        }}
        disabled={sync.isPending}
      >
        <RefreshCw className={`mr-2 h-4 w-4 ${sync.isPending ? "animate-spin" : ""}`} />
        {sync.isPending ? "Syncing…" : "Sync inventory"}
      </Button>
    </div>
  );
}
