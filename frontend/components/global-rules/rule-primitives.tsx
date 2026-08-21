"use client";

import { AlertTriangle, Info } from "lucide-react";
import { useId, type ReactNode } from "react";

import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";
import { SCOPE_IDENTIFIER, type RuleScope } from "@/services/global-rules";

/**
 * Small building blocks shared by the pricing and shipping rule forms.
 *
 * These exist rather than repeating markup because the accessibility wiring is
 * the part that gets dropped when it is retyped: a field needs its label bound
 * by `id`, its description and error both referenced from `aria-describedby`,
 * and `aria-invalid` toggled. Done by hand per field, it is done inconsistently
 * — and the omission is invisible to anyone not using a screen reader.
 *
 * `components/ui/form.tsx` covers the same ground for react-hook-form. These
 * forms are controlled directly (their field visibility depends on a strategy
 * selector, and their values are strings destined for a Decimal API), so they
 * use these instead of adding a resolver layer that would buy nothing.
 */

export interface FieldProps {
  label: string;
  description?: ReactNode;
  error?: string;
  required?: boolean;
  className?: string;
  children: (props: {
    id: string;
    "aria-describedby": string | undefined;
    "aria-invalid": boolean;
  }) => ReactNode;
}

export function Field({
  label,
  description,
  error,
  required,
  className,
  children,
}: FieldProps) {
  const id = useId();
  const descriptionId = description ? `${id}-description` : undefined;
  const errorId = error ? `${id}-error` : undefined;
  const describedBy = [descriptionId, errorId].filter(Boolean).join(" ") || undefined;

  return (
    <div className={cn("space-y-1.5", className)}>
      <Label htmlFor={id}>
        {label}
        {required && (
          <span className="ml-1 text-destructive" aria-hidden="true">
            *
          </span>
        )}
      </Label>
      {children({
        id,
        "aria-describedby": describedBy,
        "aria-invalid": Boolean(error),
      })}
      {description && (
        <p id={descriptionId} className="text-xs text-muted-foreground">
          {description}
        </p>
      )}
      {error && (
        <p id={errorId} role="alert" className="text-xs font-medium text-destructive">
          {error}
        </p>
      )}
    </div>
  );
}

/**
 * A decimal money/percentage input.
 *
 * `inputMode="decimal"` rather than `type="number"`: a number input silently
 * discards what it cannot parse, so a half-typed "1." vanishes mid-keystroke,
 * and its scroll-wheel behaviour changes prices by accident. The value stays a
 * string all the way to the API, which speaks Decimal.
 */
export function DecimalInput({
  value,
  onChange,
  suffix,
  ...props
}: {
  value: string;
  onChange: (value: string) => void;
  suffix?: string;
  id?: string;
  placeholder?: string;
  "aria-describedby"?: string;
  "aria-invalid"?: boolean;
}) {
  return (
    <div className="relative">
      <Input
        {...props}
        inputMode="decimal"
        autoComplete="off"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className={cn(suffix && "pr-10")}
      />
      {suffix && (
        <span
          className="pointer-events-none absolute inset-y-0 right-3 flex items-center text-xs text-muted-foreground"
          aria-hidden="true"
        >
          {suffix}
        </span>
      )}
    </div>
  );
}

/** A native select. The app has no Radix select; this keeps one idiom. */
export function Select<T extends string>({
  value,
  onChange,
  options,
  ...props
}: {
  value: T;
  onChange: (value: T) => void;
  options: ReadonlyArray<{ value: T; label: string }>;
  id?: string;
  "aria-describedby"?: string;
  "aria-invalid"?: boolean;
}) {
  return (
    <select
      {...props}
      value={value}
      onChange={(event) => onChange(event.target.value as T)}
      className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
    >
      {options.map((option) => (
        <option key={option.value} value={option.value}>
          {option.label}
        </option>
      ))}
    </select>
  );
}

/**
 * A labelled switch built on a real checkbox.
 *
 * The input is visually hidden but still focusable and still announced, so
 * keyboard and screen-reader behaviour comes from the platform rather than
 * being reimplemented with `role="switch"` and key handlers.
 */
export function Toggle({
  checked,
  onChange,
  label,
  description,
  disabled,
}: {
  checked: boolean;
  onChange: (checked: boolean) => void;
  label: string;
  description?: ReactNode;
  disabled?: boolean;
}) {
  const id = useId();
  const descriptionId = description ? `${id}-description` : undefined;
  return (
    <div className="flex items-start gap-3">
      <span className="relative mt-0.5 inline-flex shrink-0">
        <input
          id={id}
          type="checkbox"
          role="switch"
          checked={checked}
          disabled={disabled}
          aria-describedby={descriptionId}
          onChange={(event) => onChange(event.target.checked)}
          className="peer h-6 w-11 cursor-pointer appearance-none rounded-full bg-muted transition-colors checked:bg-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
        />
        <span
          aria-hidden="true"
          className="pointer-events-none absolute left-0.5 top-0.5 h-5 w-5 rounded-full bg-background shadow transition-transform peer-checked:translate-x-5"
        />
      </span>
      <div className="min-w-0 space-y-0.5">
        <Label htmlFor={id} className="cursor-pointer">
          {label}
        </Label>
        {description && (
          <p id={descriptionId} className="text-xs text-muted-foreground">
            {description}
          </p>
        )}
      </div>
    </div>
  );
}

/**
 * Trim a backend Decimal string for display.
 *
 * The API sends money and percentages at four decimal places, which is right
 * for storage and wrong for a merchant: "50.0000% markup" reads as precision
 * nobody asked for. Trailing zeros are dropped, down to a floor the caller
 * sets — money keeps two so 21.0000 renders as 21.00 rather than 21.
 *
 * Purely presentational. The value sent back to the API is always the string
 * the merchant typed or the one the server supplied, never this.
 */
export function trimDecimal(value: string, minDecimals = 0): string {
  const raw = value.trim();
  if (!/^-?\d+(\.\d+)?$/.test(raw)) return value;
  const parts = raw.split(".");
  const whole = parts[0] ?? raw;
  const padded = (parts[1] ?? "").replace(/0+$/, "").padEnd(minDecimals, "0");
  return padded ? `${whole}.${padded}` : whole;
}

export function StatusBadge({ active }: { active: boolean }) {
  return (
    <Badge variant={active ? "default" : "outline"}>
      {active ? "Active" : "Inactive"}
    </Badge>
  );
}

export const SCOPE_LABEL: Record<RuleScope, string> = {
  variant: "Variant",
  product: "Product",
  category: "Category",
  store: "Store",
  global: "Global",
};

/** Narrowest first — the order precedence actually resolves in. */
export const SCOPE_ORDER: readonly RuleScope[] = [
  "variant",
  "product",
  "category",
  "store",
  "global",
];

export function scopeIdentifierLabel(scope: RuleScope): string | null {
  const key = SCOPE_IDENTIFIER[scope];
  if (!key) return null;
  return {
    storeId: "Store ID",
    categoryId: "Category ID",
    productId: "Product ID",
    variantId: "Variant ID",
  }[key];
}

export function Callout({
  tone = "info",
  title,
  children,
}: {
  tone?: "info" | "warning";
  title?: string;
  children: ReactNode;
}) {
  const Icon = tone === "warning" ? AlertTriangle : Info;
  return (
    <div
      className={cn(
        "flex gap-3 rounded-md border p-3 text-sm",
        tone === "warning"
          ? "border-amber-500/40 bg-amber-500/10 text-amber-900 dark:text-amber-200"
          : "border-border bg-muted/40 text-muted-foreground",
      )}
    >
      <Icon className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
      <div className="min-w-0 space-y-1">
        {title && <p className="font-medium text-foreground">{title}</p>}
        <div className="[&_p]:leading-relaxed">{children}</div>
      </div>
    </div>
  );
}

/**
 * The single most valuable sentence on this screen.
 *
 * Markup and margin at the same percentage produce very different prices, and
 * merchants routinely enter one meaning the other. The worked example is not
 * decoration — it is the fastest way to make the difference concrete, and the
 * numbers are fixed arithmetic on a stated cost, not a live calculation, so it
 * carries no risk of disagreeing with the engine.
 */
export function MarkupVersusMargin() {
  return (
    <Callout title="Markup and gross margin are not the same">
      <p>
        Markup is profit as a share of <strong>cost</strong>. Gross margin is
        profit as a share of the <strong>selling price</strong>. At the same
        percentage, margin always produces the higher price.
      </p>
      <p className="mt-2 font-mono text-xs" data-testid="markup-margin-example">
        £10 landed cost — 50% markup = £15 · 50% gross margin = £20
      </p>
    </Callout>
  );
}
