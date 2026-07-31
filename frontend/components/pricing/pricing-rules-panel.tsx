"use client";

import { Tags } from "lucide-react";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  useCreatePricingRule,
  usePricingRules,
  type PricingStrategy,
} from "@/services/pricing";

export function PricingRulesPanel() {
  const { data, isLoading, isError, refetch } = usePricingRules({
    page: 1,
    size: 50,
  });
  const create = useCreatePricingRule();
  const [name, setName] = useState("Default markup");
  const [strategy, setStrategy] = useState<PricingStrategy>("percentage_markup");
  const [markupPercent, setMarkupPercent] = useState("30");
  const [minProfit, setMinProfit] = useState("");
  const [maxPrice, setMaxPrice] = useState("");
  const [formError, setFormError] = useState<string | null>(null);

  if (isError) {
    return (
      <ErrorState
        title="Could not load pricing rules"
        onRetry={() => void refetch()}
      />
    );
  }

  const rules = data?.items ?? [];

  return (
    <div className="grid gap-6 lg:grid-cols-[320px_1fr]">
      <form
        className="space-y-4 rounded-md border p-4"
        onSubmit={(event) => {
          event.preventDefault();
          setFormError(null);
          create.mutate(
            {
              name,
              scope: "global",
              strategy,
              markupPercent:
                strategy === "percentage_markup" ? markupPercent : undefined,
              markupFixed: strategy === "fixed_markup" ? markupPercent : undefined,
              minProfit: minProfit || undefined,
              maxPrice: maxPrice || undefined,
            },
            {
              onError: (err) => {
                setFormError(err instanceof Error ? err.message : "Create failed.");
              },
              onSuccess: () => setFormError(null),
            },
          );
        }}
      >
        <h2 className="text-sm font-semibold">New rule</h2>
        <div className="space-y-2">
          <Label htmlFor="rule-name">Name</Label>
          <Input
            id="rule-name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            required
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="rule-strategy">Strategy</Label>
          <select
            id="rule-strategy"
            aria-label="Strategy"
            value={strategy}
            onChange={(event) =>
              setStrategy(event.target.value as PricingStrategy)
            }
            className="h-9 w-full rounded-md border border-input bg-transparent px-3 text-sm"
          >
            <option value="percentage_markup">Percentage markup</option>
            <option value="fixed_markup">Fixed markup</option>
            <option value="tiered">Tiered</option>
          </select>
        </div>
        <div className="space-y-2">
          <Label htmlFor="rule-markup">
            {strategy === "fixed_markup" ? "Fixed markup" : "Markup %"}
          </Label>
          <Input
            id="rule-markup"
            value={markupPercent}
            onChange={(event) => setMarkupPercent(event.target.value)}
            required={strategy !== "tiered"}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="rule-min-profit">Minimum profit (optional)</Label>
          <Input
            id="rule-min-profit"
            value={minProfit}
            onChange={(event) => setMinProfit(event.target.value)}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="rule-max-price">Maximum price (optional)</Label>
          <Input
            id="rule-max-price"
            value={maxPrice}
            onChange={(event) => setMaxPrice(event.target.value)}
          />
        </div>
        {formError && <p className="text-sm text-destructive">{formError}</p>}
        <Button type="submit" disabled={create.isPending} className="w-full">
          {create.isPending ? "Creating…" : "Create rule"}
        </Button>
      </form>

      {isLoading ? (
        <Skeleton className="h-48 w-full" />
      ) : rules.length === 0 ? (
        <EmptyState
          icon={Tags}
          title="No pricing rules"
          description="Create a global markup rule, preview, then apply."
        />
      ) : (
        <div className="rounded-md border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead>Scope</TableHead>
                <TableHead>Strategy</TableHead>
                <TableHead>Markup</TableHead>
                <TableHead>Guards</TableHead>
                <TableHead>Active</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rules.map((rule) => (
                <TableRow key={rule.id}>
                  <TableCell className="font-medium">{rule.name}</TableCell>
                  <TableCell>{rule.scope}</TableCell>
                  <TableCell>{rule.strategy}</TableCell>
                  <TableCell>
                    {rule.markupPercent
                      ? `${rule.markupPercent}%`
                      : rule.markupFixed ?? "—"}
                  </TableCell>
                  <TableCell className="text-xs text-muted-foreground">
                    {[
                      rule.minProfit ? `min profit ${rule.minProfit}` : null,
                      rule.maxPrice ? `max ${rule.maxPrice}` : null,
                    ]
                      .filter(Boolean)
                      .join(" · ") || "—"}
                  </TableCell>
                  <TableCell>
                    <Badge variant={rule.isActive ? "success" : "secondary"}>
                      {rule.isActive ? "active" : "inactive"}
                    </Badge>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </div>
  );
}
