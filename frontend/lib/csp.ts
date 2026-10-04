/**
 * The Content-Security-Policy, and the nonce that makes it work without
 * `'unsafe-inline'`.
 *
 * **Why a nonce at all.** `script-src 'unsafe-inline'` permits every inline
 * script on the page, which is precisely the capability an XSS payload needs —
 * it turns the strongest directive in the policy into a no-op. Next.js does
 * emit inline scripts (the hydration bootstrap and the streamed RSC payload),
 * so they cannot simply be banned. A per-response nonce is the supported way
 * out: Next.js reads the policy from the *request* header, finds the
 * `'nonce-…'` token in `script-src`, and stamps that value onto every script
 * tag it renders. Anything an attacker injects has no nonce and does not run.
 *
 * **Why the nonce is generated per response and never reused.** A fixed nonce
 * is worse than none: it is published in the HTML of every page, so an attacker
 * reads it once and attaches it to their own injected script forever. It is
 * minted in `proxy.ts` from the Web Crypto RNG on each request, appears
 * only in the response header and the script tags of that one response, and is
 * never written to a URL, a log, a cookie or browser storage.
 *
 * **Origins.** Each one is here for a single reason, and none is a wildcard on
 * `google.com` — that would cover user-content hosts:
 *
 *   script-src   accounts.google.com     — the GIS client library
 *   style-src    accounts.google.com     — the stylesheet that library loads
 *   frame-src    accounts.google.com     — the account chooser it opens
 *   connect-src  accounts.google.com     — the calls GIS makes while signing in
 *   img-src      *.googleusercontent.com — avatars on the account chooser
 *   img-src      SUPPLIER_IMAGE_ORIGINS  — product photos, see below
 *
 * `'unsafe-inline'` remains on **styles** only. Next.js injects critical CSS
 * inline and offers no nonce for it; inline CSS is not script execution, and
 * the exchange is a narrow style risk for the removal of the script one.
 *
 * **Cost, stated plainly.** A per-request nonce means the HTML cannot be
 * prerendered at build time, so every page renders dynamically. See
 * `app/layout.tsx`.
 */

/** Bytes of entropy behind each nonce. 128 bits, the CSP3 recommendation. */
const NONCE_BYTES = 16;

/**
 * A fresh nonce. Web Crypto, which the Edge runtime provides — `Math.random()`
 * is predictable and would hand an attacker the value.
 */
export function createNonce(): string {
  const bytes = new Uint8Array(NONCE_BYTES);
  crypto.getRandomValues(bytes);
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary);
}

/** The API the browser is allowed to talk to. Inlined at build time. */
export const API_ORIGIN = new URL(
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000",
).origin;

export const GOOGLE_IDENTITY_ORIGIN = "https://accounts.google.com";

/**
 * Where product photos are served from. Imported products keep their
 * supplier's image URLs (gallery, variants, and `<img>` tags inside the
 * supplier description), so without these every product image is blocked.
 *
 * Named hosts, not `https:`. Supplier description HTML is third-party
 * content: an open `img-src` would let any description load tracking
 * pixels that report each merchant's visit. Add a host here when a new
 * supplier or channel is imported from.
 *
 *   *.alicdn.com           AliExpress image CDN (ae01–ae04)
 *   *.aliexpress-media.com AliExpress's newer image host
 *   cdn.shopify.com        products imported from a Shopify store
 */
export const SUPPLIER_IMAGE_ORIGINS = [
  "https://*.alicdn.com",
  "https://*.aliexpress-media.com",
  "https://cdn.shopify.com",
] as const;

/**
 * Webpack's development runtime evaluates module factories via `eval`.
 * Without `'unsafe-eval'`, `next dev` downloads every chunk and then refuses
 * to run them — React never hydrates, the login form stays a native GET, and
 * credentials leak into the address bar. Production builds do not use eval,
 * so this token is omitted outside development.
 */
const SCRIPT_SRC_EVAL =
  process.env.NODE_ENV === "development" ? " 'unsafe-eval'" : "";

/** The full policy for one response. */
export function buildContentSecurityPolicy(nonce: string): string {
  return [
    "default-src 'self'",
    "base-uri 'self'",
    "object-src 'none'",
    "frame-ancestors 'none'",
    "form-action 'self'",
    // No 'unsafe-inline'. The nonce is what lets Next.js hydrate.
    // 'unsafe-eval' only in development — see SCRIPT_SRC_EVAL above.
    `script-src 'self' 'nonce-${nonce}'${SCRIPT_SRC_EVAL} ${GOOGLE_IDENTITY_ORIGIN}`,
    `style-src 'self' 'unsafe-inline' ${GOOGLE_IDENTITY_ORIGIN}`,
    `img-src 'self' data: https://*.googleusercontent.com ${SUPPLIER_IMAGE_ORIGINS.join(" ")}`,
    "font-src 'self' data:",
    `connect-src 'self' ${API_ORIGIN} ${GOOGLE_IDENTITY_ORIGIN}`,
    `frame-src ${GOOGLE_IDENTITY_ORIGIN}`,
  ].join("; ");
}
