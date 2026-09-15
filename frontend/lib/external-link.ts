/**
 * Allowlist for links that leave the application.
 *
 * Reused from the reviewed historical UX-L2C implementation (UX-L2D-GATE-04):
 * only HTTPS URLs on `*.myshopify.com` hosts are ever rendered as external
 * links, and only when the server supplied them. A URL is never built from a
 * shop domain or a handle, and anything carrying userinfo, a non-default
 * port, or the bare apex host is refused — the empty result renders as "no
 * link", never as a fallback guess.
 */

const SHOPIFY_HOST_PATTERN = /^([a-z0-9-]+\.)+myshopify\.com$/i;

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

/** The `rel` every external link carries: no opener, no referrer. */
export const EXTERNAL_LINK_REL = "noopener noreferrer";
