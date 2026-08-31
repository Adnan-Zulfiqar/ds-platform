/**
 * The legal documents a signup must acknowledge, and their versions.
 *
 * These mirror `backend/app/core/legal.py`. The backend compares what arrives
 * against its own values and refuses a mismatch, so a page left open across a
 * wording change cannot record agreement to text nobody saw — this file is the
 * convenience copy, never the authority.
 *
 * The Terms exist at `/terms` as a **draft awaiting solicitor review**. The
 * `draft-` prefix is deliberate: a bare date would read as a published version
 * to anybody looking up a stored acceptance later, and what they would be
 * looking up is a draft.
 *
 * `TERMS_PUBLISHED` is false, and the backend acts on it — a deployed
 * environment refuses registration outright rather than recording agreement to
 * unapproved text. This copy exists so the page can label itself honestly, not
 * so the client can decide anything.
 */

export const PRIVACY_NOTICE_VERSION = "2026-08-28";
export const TERMS_VERSION = "draft-2026-08-31";
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
