"use client";

import { useState, type FormEvent } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useCreateStore, type StorePlatform } from "@/services/stores";

const PLATFORMS: Array<{ value: StorePlatform; label: string }> = [
  { value: "manual", label: "Manual" },
  { value: "shopify", label: "Shopify" },
  { value: "woocommerce", label: "WooCommerce" },
  { value: "ebay", label: "eBay" },
  { value: "etsy", label: "Etsy" },
  { value: "tiktok_shop", label: "TikTok Shop" },
];

function slugify(value: string): string {
  return value
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "")
    .slice(0, 64);
}

export function CreateStoreDialog() {
  const create = useCreateStore();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [platform, setPlatform] = useState<StorePlatform>("manual");
  const [currency, setCurrency] = useState("USD");
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      await create.mutateAsync({
        name,
        slug: slug || slugify(name),
        platform,
        currency,
      });
      setOpen(false);
      setName("");
      setSlug("");
      setPlatform("manual");
      setCurrency("USD");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not create store.");
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button>Add store</Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add store</DialogTitle>
          <DialogDescription>
            Register a sales channel. Credentials are encrypted at rest and never
            returned by the API.
          </DialogDescription>
        </DialogHeader>
        <form className="space-y-4" onSubmit={(event) => void onSubmit(event)}>
          <div className="space-y-2">
            <Label htmlFor="store-name">Name</Label>
            <Input
              id="store-name"
              value={name}
              onChange={(event) => {
                setName(event.target.value);
                if (!slug) setSlug(slugify(event.target.value));
              }}
              required
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="store-slug">Slug</Label>
            <Input
              id="store-slug"
              value={slug}
              onChange={(event) => setSlug(event.target.value)}
              pattern="^[a-z0-9]+(?:-[a-z0-9]+)*$"
              required
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="store-platform">Platform</Label>
            <select
              id="store-platform"
              aria-label="Platform"
              value={platform}
              onChange={(event) => setPlatform(event.target.value as StorePlatform)}
              className="h-9 w-full rounded-md border border-input bg-transparent px-3 text-sm shadow-sm"
            >
              {PLATFORMS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </div>
          <div className="space-y-2">
            <Label htmlFor="store-currency">Currency</Label>
            <Input
              id="store-currency"
              value={currency}
              onChange={(event) => setCurrency(event.target.value.toUpperCase())}
              maxLength={3}
              required
            />
          </div>
          {error && <p className="text-sm text-destructive">{error}</p>}
          <DialogFooter>
            <Button type="submit" disabled={create.isPending}>
              {create.isPending ? "Creating…" : "Create store"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
