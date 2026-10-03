"use client";

import { useState, type FormEvent } from "react";

import { ChannelCard } from "@/components/integrations/channel-card";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ApiError } from "@/lib/api-client";
import { deriveWooCommerceChannel } from "@/lib/channel-state";
import { useAuth } from "@/providers/auth-provider";
import {
  useConnectWooCommerce,
  useDisconnectWooCommerce,
  useImportWooCommerceOrders,
  useWooCommerceStores,
} from "@/services/integrations";

/**
 * Track E7 W1: WooCommerce stores connect with REST API keys the merchant
 * creates in their own WordPress admin. The server checks the keys against
 * the store before saving them and never sends them back; this form clears
 * them from state as soon as the request finishes.
 *
 * Publishing happens from Review & publish (W2), and price and stock follow
 * automatically (W3). Orders are pulled on demand here (W4a); live order
 * webhooks are a later stage.
 */
export function WooCommerceCard() {
  const { hasRole } = useAuth();
  const canManage = hasRole("owner") || hasRole("admin");
  const stores = useWooCommerceStores();
  const connect = useConnectWooCommerce();
  const disconnect = useDisconnectWooCommerce();
  const importOrders = useImportWooCommerceOrders();
  const [imported, setImported] = useState<{ storeId: string; text: string } | null>(null);
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ name: "", siteUrl: "", consumerKey: "", consumerSecret: "" });

  const state = deriveWooCommerceChannel(stores);

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    connect.mutate(form, {
      onSettled: () => setForm((f) => ({ ...f, consumerKey: "", consumerSecret: "" })),
      onSuccess: () => {
        setOpen(false);
        setForm({ name: "", siteUrl: "", consumerKey: "", consumerSecret: "" });
      },
    });
  }

  const error =
    connect.error instanceof ApiError
      ? connect.error.message
      : connect.error
        ? "Could not connect the store."
        : null;

  return (
    <ChannelCard
      id="woocommerce"
      name="WooCommerce"
      description="Link a WooCommerce store with REST API keys. Publish from Review & publish; price and stock then follow your edits."
      state={state}
      readOnly={!canManage}
      readOnlyHint="Only owners and admins can connect stores."
      data-testid="channel-woocommerce"
    >
      <div className="space-y-4 text-sm">
        {(stores.data ?? []).length > 0 && (
          <ul className="divide-y rounded-lg border" data-testid="woocommerce-stores">
            {(stores.data ?? []).map((store) => (
              <li key={store.id} className="flex items-center gap-3 p-3">
                <div className="min-w-0 flex-1">
                  <p className="truncate font-medium">{store.name}</p>
                  <p className="truncate text-muted-foreground">
                    {store.storefrontUrl} · {store.currency} · {store.status}
                  </p>
                  {imported?.storeId === store.id && (
                    <p className="text-muted-foreground" role="status">
                      {imported.text}
                    </p>
                  )}
                </div>
                {canManage && store.status === "connected" && (
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={importOrders.isPending}
                    onClick={() =>
                      importOrders.mutate(store.id, {
                        onSuccess: (r) =>
                          setImported({
                            storeId: store.id,
                            text: `Imported ${r.fetched} orders (${r.created} new, ${r.updated} updated).`,
                          }),
                        onError: () =>
                          setImported({ storeId: store.id, text: "Could not import orders." }),
                      })
                    }
                  >
                    Import recent orders
                  </Button>
                )}
                {canManage && store.status === "connected" && (
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={disconnect.isPending}
                    onClick={() => disconnect.mutate(store.id)}
                  >
                    Disconnect
                  </Button>
                )}
              </li>
            ))}
          </ul>
        )}

        {canManage && !open && (
          <Button variant="outline" onClick={() => setOpen(true)}>
            Connect a WooCommerce store
          </Button>
        )}

        {canManage && open && (
          <form className="space-y-3" onSubmit={onSubmit} noValidate>
            <p className="text-muted-foreground">
              In WordPress: WooCommerce → Settings → Advanced → REST API → Add key, with
              Read/Write permission. Paste the key and secret here.
            </p>
            <div className="space-y-1">
              <Label htmlFor="woo-name">Store name</Label>
              <Input
                id="woo-name"
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="woo-url">Store address</Label>
              <Input
                id="woo-url"
                type="url"
                placeholder="https://shop.example.com"
                value={form.siteUrl}
                onChange={(e) => setForm({ ...form, siteUrl: e.target.value })}
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="woo-key">Consumer key</Label>
              <Input
                id="woo-key"
                autoComplete="off"
                spellCheck={false}
                value={form.consumerKey}
                onChange={(e) => setForm({ ...form, consumerKey: e.target.value })}
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="woo-secret">Consumer secret</Label>
              <Input
                id="woo-secret"
                type="password"
                autoComplete="off"
                value={form.consumerSecret}
                onChange={(e) => setForm({ ...form, consumerSecret: e.target.value })}
              />
            </div>
            {error && (
              <Alert variant="destructive">
                <AlertDescription>{error}</AlertDescription>
              </Alert>
            )}
            <div className="flex gap-2">
              <Button
                type="submit"
                disabled={
                  connect.isPending || !form.siteUrl || !form.consumerKey || !form.consumerSecret
                }
              >
                {connect.isPending ? "Checking…" : "Connect"}
              </Button>
              <Button type="button" variant="ghost" onClick={() => setOpen(false)}>
                Cancel
              </Button>
            </div>
          </form>
        )}
      </div>
    </ChannelCard>
  );
}
