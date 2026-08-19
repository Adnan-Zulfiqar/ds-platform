"use client";

import { Check, ChevronsUpDown, Loader2, X } from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent } from "react";

import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { useRuleTargets, type RuleTarget, type TargetKind } from "@/services/global-rules";

/**
 * Pick a product, variant, category or store by name.
 *
 * Built to the ARIA combobox pattern by hand rather than pulled from a
 * library: the app has no listbox primitive, and adding one for a single
 * control would be a larger dependency than the control.
 *
 * The behaviours that matter, none of which come free:
 *
 * * The input owns focus throughout. Moving focus into the list is what breaks
 *   type-ahead, because every keystroke would then have to be forwarded back.
 * * `aria-activedescendant` marks the highlighted option, so a screen reader
 *   announces it without focus ever leaving the input.
 * * Options render only while open, so a page with four scope pickers issues
 *   no requests until one is used.
 * * The **id** is what gets stored; the label is only shown. They are separate
 *   fields all the way down — a product named after a UUID must not be
 *   ambiguous.
 */

const DEBOUNCE_MS = 250;

export function TargetCombobox({
  kind,
  value,
  label,
  onChange,
  productId,
  placeholder,
  id,
  "aria-describedby": describedBy,
  "aria-invalid": invalid,
}: {
  kind: TargetKind;
  /** The stored identifier, or null. */
  value: string | null;
  /** The label last seen for `value`, so an edit form can render it. */
  label?: string | null;
  onChange: (target: { id: string; label: string } | null) => void;
  /** Narrows a variant search to one product. */
  productId?: string | null;
  placeholder?: string;
  id?: string;
  "aria-describedby"?: string;
  "aria-invalid"?: boolean;
}) {
  const listId = useId();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [debounced, setDebounced] = useState("");
  const [highlighted, setHighlighted] = useState(0);
  const [chosenLabel, setChosenLabel] = useState<string | null>(label ?? null);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => setChosenLabel(label ?? null), [label]);

  useEffect(() => {
    const timer = setTimeout(() => setDebounced(query), DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [query]);

  const results = useRuleTargets(kind, debounced, { enabled: open, productId });
  const options = useMemo<RuleTarget[]>(() => results.data?.items ?? [], [results.data]);

  // Close when focus or a click leaves the control entirely. `focusout` rather
  // than `blur` so moving between the input and an option does not close it.
  useEffect(() => {
    if (!open) return;
    const onDocument = (event: Event) => {
      if (!containerRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDocument);
    document.addEventListener("focusin", onDocument);
    return () => {
      document.removeEventListener("mousedown", onDocument);
      document.removeEventListener("focusin", onDocument);
    };
  }, [open]);

  function choose(option: RuleTarget) {
    onChange({ id: option.id, label: option.label });
    setChosenLabel(option.label);
    setQuery("");
    setOpen(false);
  }

  function clear() {
    onChange(null);
    setChosenLabel(null);
    setQuery("");
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (!open) {
        setOpen(true);
        return;
      }
      if (options.length === 0) return;
      setHighlighted((current) => {
        const next = event.key === "ArrowDown" ? current + 1 : current - 1;
        return (next + options.length) % options.length;
      });
      return;
    }
    if (event.key === "Enter" && open) {
      const option = options[highlighted];
      if (option) {
        event.preventDefault();
        choose(option);
      }
      return;
    }
    if (event.key === "Escape" && open) {
      event.preventDefault();
      setOpen(false);
    }
  }

  const activeId = open && options[highlighted] ? `${listId}-${highlighted}` : undefined;

  return (
    <div ref={containerRef} className="relative">
      <div className="relative">
        <Input
          id={id}
          role="combobox"
          aria-expanded={open}
          aria-controls={listId}
          aria-autocomplete="list"
          aria-activedescendant={activeId}
          aria-describedby={describedBy}
          aria-invalid={invalid}
          autoComplete="off"
          value={open ? query : (chosenLabel ?? "")}
          placeholder={chosenLabel ? undefined : (placeholder ?? "Search by name…")}
          onChange={(event) => {
            setQuery(event.target.value);
            setHighlighted(0);
            if (!open) setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          onKeyDown={onKeyDown}
          className="pr-16"
          data-testid={`target-combobox-${kind}`}
        />
        <div className="absolute inset-y-0 right-2 flex items-center gap-1">
          {value && (
            <button
              type="button"
              onClick={clear}
              aria-label="Clear selection"
              className="flex h-6 w-6 items-center justify-center rounded text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <X className="h-3.5 w-3.5" aria-hidden="true" />
            </button>
          )}
          {results.isFetching ? (
            <Loader2
              className="h-3.5 w-3.5 animate-spin text-muted-foreground"
              aria-hidden="true"
            />
          ) : (
            <ChevronsUpDown
              className="h-3.5 w-3.5 text-muted-foreground"
              aria-hidden="true"
            />
          )}
        </div>
      </div>

      {/* Always rendered so assistive technology can find it by id; emptied
          and hidden when closed rather than unmounted. */}
      <ul
        id={listId}
        role="listbox"
        aria-label={`${kind} results`}
        className={cn(
          "absolute z-50 mt-1 max-h-64 w-full overflow-y-auto rounded-md border bg-popover p-1 shadow-md",
          !open && "hidden",
        )}
      >
        {open && results.isError && (
          <li role="option" aria-selected={false} aria-disabled className="px-2 py-3 text-sm text-destructive">
            The list could not be loaded. Type an identifier below instead.
          </li>
        )}
        {open && !results.isError && results.isFetching && options.length === 0 && (
          <li role="option" aria-selected={false} aria-disabled className="px-2 py-3 text-sm text-muted-foreground">
            Searching…
          </li>
        )}
        {open && !results.isError && !results.isFetching && options.length === 0 && (
          <li role="option" aria-selected={false} aria-disabled className="px-2 py-3 text-sm text-muted-foreground">
            {debounced ? `No ${kind} matches “${debounced}”.` : `No ${kind} records yet.`}
          </li>
        )}
        {open &&
          options.map((option, index) => (
            <li
              key={option.id}
              id={`${listId}-${index}`}
              role="option"
              aria-selected={option.id === value}
              // `mousedown` fires before the input's blur, so the choice
              // registers instead of the list closing out from under it.
              onMouseDown={(event) => {
                event.preventDefault();
                choose(option);
              }}
              onMouseEnter={() => setHighlighted(index)}
              className={cn(
                "flex min-h-10 cursor-pointer items-center justify-between gap-2 rounded px-2 py-1.5 text-sm",
                index === highlighted && "bg-accent text-accent-foreground",
              )}
            >
              <span className="min-w-0">
                <span className="block truncate">{option.label}</span>
                {option.sublabel && (
                  <span className="block truncate text-xs text-muted-foreground">
                    {option.sublabel}
                  </span>
                )}
              </span>
              {option.id === value && (
                <Check className="h-4 w-4 shrink-0" aria-hidden="true" />
              )}
            </li>
          ))}
      </ul>
    </div>
  );
}
