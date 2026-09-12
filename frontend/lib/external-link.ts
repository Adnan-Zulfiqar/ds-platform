const SHOPIFY_HOST_PATTERN =
  /^([a-z0-9-]+\.)*myshopify\.com$/i;

/**
 * Accept only HTTPS URLs on known Shopify admin or storefront hosts.
 * Never construct URLs from handles or untrusted shop-domain strings.
 */
export function isTrustedShopifyHttpsUrl(url: string | null | undefined): url is string {
  if (!url) return false;
  try {
    const parsed = new URL(url);
    if (parsed.protocol !== "https:") return false;
    if (parsed.username || parsed.password) return false;
    if (parsed.port && parsed.port !== "443") return false;
    if (parsed.hostname === "myshopify.com") return false;
    return SHOPIFY_HOST_PATTERN.test(parsed.hostname);
  } catch {
    return false;
  }
}

export function externalLinkRel(): "noopener noreferrer" {
  return "noopener noreferrer";
}
