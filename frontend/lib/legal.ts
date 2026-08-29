/**
 * The legal documents a signup must acknowledge, and their versions.
 *
 * These mirror `backend/app/core/legal.py`. The backend compares what arrives
 * against its own values and refuses a mismatch, so a page left open across a
 * wording change cannot record agreement to text nobody saw — this file is the
 * convenience copy, never the authority.
 *
 * There is currently **no Terms of Service document**. `/privacy` exists and is
 * versioned; a Terms page does not. The acceptance flag is still required so
 * the mechanism cannot be bypassed once the document lands, but the version is
 * a sentinel and the wording below says plainly that the Terms are not yet
 * published. Inventing a link to a page that does not exist would be worse.
 */

export const PRIVACY_NOTICE_VERSION = "2026-08-28";
export const TERMS_VERSION = "unpublished";
export const TERMS_PUBLISHED = false;

/**
 * The acceptance fields for a signup request.
 *
 * `accepted` is passed in rather than assumed. An earlier version of this
 * module exported a constant with `termsAccepted: true` baked in, which meant
 * every signup asserted acceptance whether or not the person had given it --
 * recording an agreement somebody never made is worse than recording none, and
 * it made the checkbox decorative.
 *
 * The versions are always sent, so the server can refuse a stale page.
 */
export function legalAcceptance(accepted: boolean) {
  return {
    termsAccepted: accepted,
    privacyAccepted: accepted,
    termsVersion: TERMS_VERSION,
    privacyVersion: PRIVACY_NOTICE_VERSION,
  } as const;
}
