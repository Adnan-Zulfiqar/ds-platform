import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { EXTERNAL_LINK_REL, isTrustedShopifyHttpsUrl } from "@/lib/external-link";
import type { ShopifyPublishResult } from "@/types/api";

/** A successful pipeline publish (plan §15). Links only through the trusted
 * Shopify URL allowlist; no pipeline ids — the result does not carry them. */
export function PublishResultPanel({ result }: { result: ShopifyPublishResult }) {
  const admin = isTrustedShopifyHttpsUrl(result.adminUrl) ? result.adminUrl : null;
  const storefront = isTrustedShopifyHttpsUrl(result.storefrontUrl) ? result.storefrontUrl : null;

  return (
    <Alert data-testid="ai-studio-publish-ok">
      <AlertTitle>{result.updated ? "Shopify listing updated" : "Published to Shopify"}</AlertTitle>
      <AlertDescription className="space-y-1">
        <p>Shopify now shows this approved AI version. Later publishes keep it until you choose your draft text.</p>
        <p className="flex flex-wrap gap-3">
          {admin ? (
            <a className="underline" href={admin} target="_blank" rel={EXTERNAL_LINK_REL}>
              Open in Shopify admin
            </a>
          ) : null}
          {storefront ? (
            <a className="underline" href={storefront} target="_blank" rel={EXTERNAL_LINK_REL}>
              View in store
            </a>
          ) : null}
        </p>
      </AlertDescription>
    </Alert>
  );
}
