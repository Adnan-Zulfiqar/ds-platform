"use client";

import { AlertCircle, CheckCircle2, Loader2, Plus } from "lucide-react";
import { useState } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError } from "@/lib/api-client";
import {
  useCreateEbayLocation,
  useEbayListingSetup,
  useSaveEbayListingDefaults,
} from "@/services/integrations";
import type { EbayListingSetup, EbayLocation, EbayPolicy } from "@/types/api";

/**
 * EBAY-C2: which policies and warehouse eBay listings will use.
 *
 * Everything shown is read live from the seller's eBay account by the server;
 * only the choice is stored, and the server re-checks it against eBay on save.
 * Members can look; only admins can save or create a location, because both
 * decide where every future listing ships from.
 */

const SELECT_CLASS = "h-10 w-full rounded-md border bg-background px-3 text-sm disabled:opacity-60";

const MARKETPLACE_NAMES: Record<string, string> = {
  EBAY_US: "United States",
  EBAY_GB: "United Kingdom",
  EBAY_DE: "Germany",
  EBAY_AU: "Australia",
  EBAY_CA: "Canada",
  EBAY_FR: "France",
  EBAY_IT: "Italy",
  EBAY_ES: "Spain",
};

type Field = "fulfillmentPolicyId" | "paymentPolicyId" | "returnPolicyId" | "merchantLocationKey";

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

function locationLabel(location: EbayLocation): string {
  const place = [location.city, location.postalCode, location.country].filter(Boolean).join(", ");
  const name = location.name ?? location.key;
  return place ? `${name} — ${place}` : name;
}

export function EbayListingSetupPanel({
  initialMarketplace,
  canManage,
}: {
  initialMarketplace: string | null;
  canManage: boolean;
}) {
  const [marketplaceId, setMarketplaceId] = useState(
    initialMarketplace && initialMarketplace in MARKETPLACE_NAMES ? initialMarketplace : "EBAY_US",
  );
  const setup = useEbayListingSetup(marketplaceId, true);

  return (
    <section
      aria-labelledby="ebay-listing-setup-title"
      className="space-y-4 rounded-md border p-4"
      data-testid="ebay-listing-setup"
    >
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h3 id="ebay-listing-setup-title" className="text-sm font-semibold">
            Listing setup
          </h3>
          <p className="text-sm text-muted-foreground">
            The shipping, payment and return policies and the warehouse DropPilot will use when it lists on eBay.
          </p>
        </div>
        <div className="space-y-1 sm:w-56">
          <Label htmlFor="ebay-marketplace">Marketplace</Label>
          <select
            id="ebay-marketplace"
            className={SELECT_CLASS}
            value={marketplaceId}
            onChange={(event) => setMarketplaceId(event.target.value)}
          >
            {(setup.data?.supportedMarketplaces ?? Object.keys(MARKETPLACE_NAMES)).map((id) => (
              <option key={id} value={id}>
                {MARKETPLACE_NAMES[id] ?? id}
              </option>
            ))}
          </select>
        </div>
      </div>

      {setup.isPending ? (
        <div className="space-y-2" aria-busy="true">
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-full" />
        </div>
      ) : setup.isError ? (
        <Alert variant="destructive" data-testid="ebay-listing-setup-error">
          <AlertCircle className="h-4 w-4" />
          <AlertDescription>
            {errorMessage(setup.error, "Could not read your eBay policies. Please try again.")}
          </AlertDescription>
        </Alert>
      ) : (
        <SetupForm
          // Remount per marketplace so unsaved choices never leak across.
          key={marketplaceId}
          setup={setup.data}
          canManage={canManage}
        />
      )}
    </section>
  );
}

function SetupForm({ setup, canManage }: { setup: EbayListingSetup; canManage: boolean }) {
  const save = useSaveEbayListingDefaults();
  const [chosen, setChosen] = useState<Partial<Record<Field, string>>>({});
  const [saved, setSaved] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  const enabledLocations = setup.locations.filter((location) => location.enabled);
  // Derived, not copied into state: the saved default, else the first option.
  const value = (field: Field, options: { id: string }[]): string =>
    chosen[field] ?? setup.defaults?.[field] ?? options[0]?.id ?? "";

  const fulfillment = setup.fulfillmentPolicies ?? [];
  const payment = setup.paymentPolicies ?? [];
  const returns = setup.returnPolicies ?? [];
  const locationOptions = enabledLocations.map((location) => ({ id: location.key }));

  const selection = {
    fulfillmentPolicyId: value("fulfillmentPolicyId", fulfillment),
    paymentPolicyId: value("paymentPolicyId", payment),
    returnPolicyId: value("returnPolicyId", returns),
    merchantLocationKey: value("merchantLocationKey", locationOptions),
  };
  const complete = Object.values(selection).every(Boolean);

  function choose(field: Field, next: string) {
    setSaved(false);
    setChosen((current) => ({ ...current, [field]: next }));
  }

  async function handleSave() {
    setSaveError(null);
    setSaved(false);
    try {
      await save.mutateAsync({ marketplaceId: setup.marketplaceId, ...selection });
      setChosen({});
      setSaved(true);
    } catch (error) {
      setSaveError(errorMessage(error, "Could not save. Please try again."));
    }
  }

  if (!setup.businessPoliciesEnabled) {
    return (
      <Alert data-testid="ebay-policies-not-enabled">
        <AlertCircle className="h-4 w-4" />
        <AlertDescription>
          This eBay account has not turned on business policies yet. Turn them on in eBay Seller Hub (Account
          settings › Business policies), create a shipping, payment and return policy, then reload this page.
        </AlertDescription>
      </Alert>
    );
  }

  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-2">
        <PolicySelect
          id="ebay-fulfillment-policy"
          label="Shipping policy"
          policies={fulfillment}
          value={selection.fulfillmentPolicyId}
          disabled={!canManage}
          onChange={(next) => choose("fulfillmentPolicyId", next)}
        />
        <PolicySelect
          id="ebay-payment-policy"
          label="Payment policy"
          policies={payment}
          value={selection.paymentPolicyId}
          disabled={!canManage}
          onChange={(next) => choose("paymentPolicyId", next)}
        />
        <PolicySelect
          id="ebay-return-policy"
          label="Return policy"
          policies={returns}
          value={selection.returnPolicyId}
          disabled={!canManage}
          onChange={(next) => choose("returnPolicyId", next)}
        />
        <div className="space-y-1">
          {enabledLocations.length === 0 ? (
            <p className="text-sm font-medium">Ships from</p>
          ) : (
            <Label htmlFor="ebay-location">Ships from</Label>
          )}
          {enabledLocations.length === 0 ? (
            <p className="text-sm text-muted-foreground" data-testid="ebay-no-locations">
              No warehouse on this eBay account yet{canManage ? " — add one below." : "."}
            </p>
          ) : (
            <select
              id="ebay-location"
              className={SELECT_CLASS}
              value={selection.merchantLocationKey}
              disabled={!canManage}
              onChange={(event) => choose("merchantLocationKey", event.target.value)}
            >
              {enabledLocations.map((location) => (
                <option key={location.key} value={location.key}>
                  {locationLabel(location)}
                </option>
              ))}
            </select>
          )}
        </div>
      </div>

      {saveError ? (
        <Alert variant="destructive" data-testid="ebay-listing-defaults-error">
          <AlertCircle className="h-4 w-4" />
          <AlertDescription>{saveError}</AlertDescription>
        </Alert>
      ) : null}

      {canManage ? (
        <div className="flex flex-wrap items-center gap-3">
          <Button
            className="min-h-11 sm:min-h-9"
            onClick={() => void handleSave()}
            disabled={!complete || save.isPending}
          >
            {save.isPending ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin motion-reduce:animate-none" aria-hidden="true" />
            ) : null}
            Save listing defaults
          </Button>
          {saved ? (
            <span className="flex items-center text-sm text-muted-foreground" role="status" data-testid="ebay-defaults-saved">
              <CheckCircle2 className="mr-1 h-4 w-4" aria-hidden="true" />
              Saved
            </span>
          ) : !complete ? (
            <span className="text-sm text-muted-foreground">
              Each policy and a warehouse are needed before eBay will accept a listing.
            </span>
          ) : null}
        </div>
      ) : (
        <p className="text-sm text-muted-foreground">Only an owner or admin can change these.</p>
      )}

      {canManage ? <AddLocationForm /> : null}
    </div>
  );
}

function PolicySelect({
  id,
  label,
  policies,
  value,
  disabled,
  onChange,
}: {
  id: string;
  label: string;
  policies: EbayPolicy[];
  value: string;
  disabled: boolean;
  onChange: (next: string) => void;
}) {
  return (
    <div className="space-y-1">
      {policies.length === 0 ? (
        <p className="text-sm font-medium">{label}</p>
      ) : (
        <Label htmlFor={id}>{label}</Label>
      )}
      {policies.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          None on eBay for this marketplace — create one in Seller Hub.
        </p>
      ) : (
        <select
          id={id}
          className={SELECT_CLASS}
          value={value}
          disabled={disabled}
          onChange={(event) => onChange(event.target.value)}
        >
          {policies.map((policy) => (
            <option key={policy.id} value={policy.id}>
              {policy.name}
            </option>
          ))}
        </select>
      )}
    </div>
  );
}

function AddLocationForm() {
  const create = useCreateEbayLocation();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [postalCode, setPostalCode] = useState("");
  const [city, setCity] = useState("");
  const [country, setCountry] = useState("");
  const [error, setError] = useState<string | null>(null);

  const normalisedCountry = country.trim().toUpperCase();
  const valid = name.trim() !== "" && postalCode.trim() !== "" && /^[A-Z]{2}$/.test(normalisedCountry);

  async function handleCreate() {
    setError(null);
    try {
      await create.mutateAsync({
        name: name.trim(),
        postalCode: postalCode.trim(),
        city: city.trim() || undefined,
        country: normalisedCountry,
      });
      setOpen(false);
      setName("");
      setPostalCode("");
      setCity("");
      setCountry("");
    } catch (caught) {
      setError(errorMessage(caught, "Could not create the location on eBay."));
    }
  }

  if (!open) {
    return (
      <Button variant="outline" className="min-h-11 sm:min-h-9" onClick={() => setOpen(true)}>
        <Plus className="mr-2 h-4 w-4" aria-hidden="true" />
        Add a warehouse on eBay
      </Button>
    );
  }

  return (
    <div className="space-y-3 rounded-md border p-3" data-testid="ebay-add-location">
      <p className="text-sm text-muted-foreground">
        Created on your eBay account as a warehouse location. eBay keeps it even if you later disconnect.
      </p>
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="space-y-1">
          <Label htmlFor="ebay-location-name">Name</Label>
          <Input id="ebay-location-name" value={name} onChange={(event) => setName(event.target.value)} />
        </div>
        <div className="space-y-1">
          <Label htmlFor="ebay-location-postal">Postal code</Label>
          <Input
            id="ebay-location-postal"
            value={postalCode}
            onChange={(event) => setPostalCode(event.target.value)}
          />
        </div>
        <div className="space-y-1">
          <Label htmlFor="ebay-location-city">City (optional)</Label>
          <Input id="ebay-location-city" value={city} onChange={(event) => setCity(event.target.value)} />
        </div>
        <div className="space-y-1">
          <Label htmlFor="ebay-location-country">Country code</Label>
          <Input
            id="ebay-location-country"
            placeholder="US"
            maxLength={2}
            value={country}
            onChange={(event) => setCountry(event.target.value)}
          />
        </div>
      </div>
      {error ? (
        <Alert variant="destructive">
          <AlertCircle className="h-4 w-4" />
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : null}
      <div className="flex gap-2">
        <Button className="min-h-11 sm:min-h-9" onClick={() => void handleCreate()} disabled={!valid || create.isPending}>
          {create.isPending ? (
            <Loader2 className="mr-2 h-4 w-4 animate-spin motion-reduce:animate-none" aria-hidden="true" />
          ) : null}
          Create warehouse
        </Button>
        <Button variant="ghost" className="min-h-11 sm:min-h-9" onClick={() => setOpen(false)} disabled={create.isPending}>
          Cancel
        </Button>
      </div>
    </div>
  );
}
