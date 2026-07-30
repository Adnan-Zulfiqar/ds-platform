/**
 * In-memory access token storage.
 *
 * **The access token is never written to `localStorage`, `sessionStorage`, or a
 * readable cookie.** Anything in those stores is readable by any script running
 * on the page, so a single XSS payload — in our code or in any dependency —
 * exfiltrates a working credential.
 *
 * Holding it in a module variable means it lives only in the JavaScript heap and
 * vanishes on reload. The cost is that a page refresh loses it; the cure is the
 * refresh token, which sits in an httpOnly cookie the browser sends
 * automatically and script cannot read. On boot the app calls `/auth/refresh`
 * once and is signed in again.
 *
 * That exchange is the deliberate design: the credential that survives a reload
 * is the one JavaScript cannot touch, and the one JavaScript holds expires in
 * minutes.
 */

let accessToken: string | null = null;
let expiresAt: number | null = null;

/** Subscribers notified when the token is cleared, so the UI can react. */
type Listener = () => void;
const listeners = new Set<Listener>();

export function setAccessToken(token: string, expiresInSeconds: number): void {
  accessToken = token;
  // Renew slightly early so a request is not sent with a token that expires
  // in flight — clock skew and network latency both eat into the margin.
  const safetyMarginMs = 30_000;
  expiresAt = Date.now() + expiresInSeconds * 1000 - safetyMarginMs;
}

export function getAccessToken(): string | null {
  return accessToken;
}

/** Whether a token is held and still within its usable window. */
export function hasValidAccessToken(): boolean {
  return accessToken !== null && expiresAt !== null && Date.now() < expiresAt;
}

export function clearAccessToken(): void {
  accessToken = null;
  expiresAt = null;
  listeners.forEach((listener) => listener());
}

export function onTokenCleared(listener: Listener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}
