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
import { ConfirmDeleteButton } from "@/components/ui/confirm-delete-button";
import {
  useCreatePricingRule,
  useDeletePricingRule,
  usePricingRules,
  useUpdatePricingRule,
  type PricingRule,
  type PricingStrategy,
} from "@/services/pricing";

interface RuleDraft {
  id: string;
  name: string;
  markupPercent: string;
  minProfit: string;
  maxPrice: string;
}

/** Empty input means "clear the guard", which the API spells `null`. */
function money(value: string): string | null {
  const trimmed = value.trim();
  return trimmed === "" ? null : trimmed;
}

export function PricingRulesPanel() {
  const { data, isLoading, isError, refetch } = usePricingRules({
    page: 1,
    size: 50,
  });
  const create = useCreatePricingRule();
  const update = useUpdatePricingRule();
  const remove = useDeletePricingRule();
  const [draft, setDraft] = useState<RuleDraft | null>(null);
  const [rowStatus, setRowStatus] = useState<string | null>(null);
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
        <div className="space-y-2">
          {rowStatus && (
            <p className="text-sm text-muted-foreground" role="status">
              {rowStatus}
            </p>
          )}
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
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {rules.map((rule) => {
                  const editing = draft?.id === rule.id;
                  const busy = update.isPending || remove.isPending;
                  const fail = (err: unknown, fallback: string) =>
                    setRowStatus(err instanceof Error ? err.message : fallback);
                  const toggle = (row: PricingRule) =>
                    update.mutate(
                      { id: row.id, isActive: !row.isActive },
                      { onError: (err) => fail(err, "Update failed.") },
                    );
                  return (
                    <TableRow key={rule.id} data-testid="pricing-rule-row">
                      <TableCell className="font-medium">
                        {editing ? (
                          <Input
                            aria-label={`Name of ${rule.name}`}
                            value={draft.name}
                            onChange={(event) =>
                              setDraft({ ...draft, name: event.target.value })
                            }
                          />
                        ) : (
                          rule.name
                        )}
                      </TableCell>
                      <TableCell>{rule.scope}</TableCell>
                      <TableCell>{rule.strategy}</TableCell>
                      <TableCell>
                        {editing && rule.strategy === "percentage_markup" ? (
                          <Input
                            aria-label={`Markup percent of ${rule.name}`}
                            inputMode="decimal"
                            value={draft.markupPercent}
                            onChange={(event) =>
                              setDraft({ ...draft, markupPercent: event.target.value })
                            }
                          />
                        ) : rule.markupPercent ? (
                          `${rule.markupPercent}%`
                        ) : (
                          (rule.markupFixed ?? "—")
                        )}
                      </TableCell>
                      <TableCell className="text-xs text-muted-foreground">
                        {editing ? (
                          <div className="flex gap-1">
                            <Input
                              aria-label={`Min profit of ${rule.name}`}
                              placeholder="min profit"
                              inputMode="decimal"
                              value={draft.minProfit}
                              onChange={(event) =>
                                setDraft({ ...draft, minProfit: event.target.value })
                              }
                            />
                            <Input
                              aria-label={`Max price of ${rule.name}`}
                              placeholder="max price"
                              inputMode="decimal"
                              value={draft.maxPrice}
                              onChange={(event) =>
                                setDraft({ ...draft, maxPrice: event.target.value })
                              }
                            />
                          </div>
                        ) : (
                          [
                            rule.minProfit ? `min profit ${rule.minProfit}` : null,
                            rule.maxPrice ? `max ${rule.maxPrice}` : null,
                          ]
                            .filter(Boolean)
                            .join(" · ") || "—"
                        )}
                      </TableCell>
                      <TableCell>
                        <Badge variant={rule.isActive ? "success" : "secondary"}>
                          {rule.isActive ? "active" : "inactive"}
                        </Badge>
                      </TableCell>
                      <TableCell className="space-x-1 whitespace-nowrap text-right">
                        {editing ? (
                          <>
                            <Button
                              size="sm"
                              disabled={busy || draft.name.trim() === ""}
                              onClick={() =>
                                update.mutate(
                                  {
                                    id: draft.id,
                                    name: draft.name,
                                    markupPercent:
                                      rule.strategy === "percentage_markup"
                                        ? money(draft.markupPercent)
                                        : undefined,
                                    minProfit: money(draft.minProfit),
                                    maxPrice: money(draft.maxPrice),
                                  },
                                  {
                                    onSuccess: () => {
                                      setDraft(null);
                                      setRowStatus("Rule updated.");
                                    },
                                    onError: (err) => fail(err, "Update failed."),
                                  },
                                )
                              }
                            >
                              Save
                            </Button>
                            <Button
                              size="sm"
                              variant="ghost"
                              disabled={busy}
                              onClick={() => setDraft(null)}
                            >
                              Cancel
                            </Button>
                          </>
                        ) : (
                          <>
                            <Button
                              size="sm"
                              variant="ghost"
                              disabled={busy}
                              aria-label={`Edit ${rule.name}`}
                              onClick={() =>
                                setDraft({
                                  id: rule.id,
                                  name: rule.name,
                                  markupPercent: rule.markupPercent ?? "",
                                  minProfit: rule.minProfit ?? "",
                                  maxPrice: rule.maxPrice ?? "",
                                })
                              }
                            >
                              Edit
                            </Button>
                            <Button
                              size="sm"
                              variant="ghost"
                              disabled={busy}
                              aria-label={`${rule.isActive ? "Pause" : "Resume"} ${rule.name}`}
                              onClick={() => toggle(rule)}
                            >
                              {rule.isActive ? "Pause" : "Resume"}
                            </Button>
                            <ConfirmDeleteButton
                              label={rule.name}
                              disabled={busy}
                              onConfirm={() =>
                                remove.mutate(rule.id, {
                                  onSuccess: () => setRowStatus(`Deleted "${rule.name}".`),
                                  onError: (err) => fail(err, "Delete failed."),
                                })
                              }
                            />
                          </>
                        )}
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </div>
        </div>
      )}
    </div>
  );
}
