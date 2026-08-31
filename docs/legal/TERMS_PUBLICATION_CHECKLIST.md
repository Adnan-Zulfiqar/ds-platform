# Terms of Service — publication checklist and version procedure

The Terms are a draft. This is what has to be true before they are not, and how
a version changes once they are.

---

## 1. How the draft state is enforced

Not by convention. Three mechanisms, each tested.

| Mechanism | Where | Effect |
|---|---|---|
| `TERMS_VERSION = "draft-2026-08-31"` | `backend/app/core/legal.py` | The stored version says on its face that it is a draft. Anybody auditing an acceptance row a year from now reads `draft-` and knows what was agreed |
| `TERMS_PUBLISHED = False` | `backend/app/core/legal.py` | `LegalAcceptance.require_valid` raises `TermsNotPublishedError` in any **deployed** environment, before any other check. A production signup cannot record agreement to unapproved text |
| Server-side version comparison | `LegalAcceptance.require_valid` | The submitted version must equal the server's constant. A client cannot name its own version, and a client built before this milestone — which would send `"unpublished"` — is refused |

Local and test environments are exempt from the publication gate so the flow can
be built and exercised. The exemption is on `Environment.is_deployed`, the same
predicate every other production guard uses.

**The `/terms` page labels itself.** While `TERMS_PUBLISHED` is false it renders
a "Draft — pending legal review" banner and is `noindex`. Both flip in the same
change that publishes.

## 2. Publication checklist

Nothing here may be ticked by the engineer who wrote the draft.

### Legal

- [ ] A solicitor has reviewed the full text of `/terms`.
- [ ] Every question in `TERMS_LEGAL_REVIEW.md` §4 has a recorded answer.
- [ ] The liability cap, the £100 floor and the data-loss exclusion are approved
      as reasonable for a B2B standard-terms contract.
- [ ] The B2B-only position is confirmed as matching how the service is actually
      sold and marketed.
- [ ] Marketplace names in §8 are confirmed as permissible.
- [ ] The registered-office and jurisdiction wording is confirmed against the
      Companies House record on the day of publication.

### Product truth

- [ ] `TERMS_PRODUCT_AUDIT.md` re-checked against the tree being published — no
      clause promises a capability that has since been removed, and no
      capability added since drafting contradicts a stated absence.
- [ ] If billing has shipped: the sentence "No paid Plan is on sale at the date
      of this version" is removed **in the same change**.
- [ ] If backups have shipped: §21 and the privacy notice updated together.
- [ ] If data export has shipped: §23 updated.
- [ ] If suspension enforcement has shipped: §22 re-read (a reserved right that
      becomes a capability needs no edit, but should be confirmed).

### Governance dependencies

- [ ] ICO assessment completed and registration obtained, or a recorded decision
      that it is not required — `docs/governance/ICO_REGISTRATION_GATE.md`.
- [ ] Data processing agreements in place with Resend and any other processor,
      and international transfer assessments completed —
      `docs/governance/SUBPROCESSORS.md`.
- [ ] A backup regime exists, or §21's statement that none exists is still true
      — `docs/governance/BACKUPS.md`.

### Technical

- [ ] `TERMS_VERSION` changed from `draft-YYYY-MM-DD` to the approved published
      identifier, in **both** `backend/app/core/legal.py` and
      `frontend/lib/legal.ts`.
- [ ] `TERMS_PUBLISHED` set to `True` in both.
- [ ] The `/terms` metadata changed to `robots: { index: true, follow: true }`.
- [ ] The draft banner disappears (it is conditional on `TERMS_PUBLISHED`; no
      edit needed, but confirm it is gone in the built page).
- [ ] `LAST_UPDATED` on the page set to the approval date.
- [ ] The focused Terms tests updated: the assertions that pin `draft-` and the
      banner must be inverted, not deleted.
- [ ] Production frontend built and deployed at the real hostname.
- [ ] Production configuration reviewed — the publication gate stops refusing
      registration the moment `TERMS_PUBLISHED` is true, so everything else must
      be ready first.

## 3. Version and change procedure

**The version identifier is server-authoritative.** It lives in
`backend/app/core/legal.py`. `frontend/lib/legal.ts` carries a copy so the page
can label itself and the client can send what it saw; the server refuses
anything that does not match its own constant. The copy is a convenience and
never the authority — the same arrangement the privacy notice uses.

For any change to the wording:

1. Edit `/terms` and `LAST_UPDATED`.
2. Choose a new identifier. Published versions use a date, `YYYY-MM-DD`. Drafts
   keep the `draft-` prefix.
3. Change it in `backend/app/core/legal.py` **and** `frontend/lib/legal.ts` in
   the same commit. They must never disagree: the server would refuse every
   registration until the frontend was rebuilt, because `NEXT_PUBLIC` values and
   module constants are baked in at build time.
4. Deploy backend and frontend together.
5. Existing users are unaffected. Their stored `terms_version` records what they
   accepted, and this milestone deliberately adds no backfill: a stored
   acceptance is a record of what happened, not a field to tidy.
6. For a **material** change, §29 of the Terms commits to reasonable notice —
   ordinarily at least 30 days — by email or in the application, with the right
   to cancel before it takes effect. Decide whether the change is material
   *before* deploying, because the notice period runs from notification.

### Re-acceptance

There is currently **no re-acceptance flow** for existing users. If a change
requires positive re-acceptance rather than notice, that is a feature to build —
see `TERMS_LEGAL_REVIEW.md` §4 question 10.

## 4. Evidence that the versions are server-authoritative

Pinned by `backend/tests/integration/test_legal_t1_terms.py`:

* an invented `termsVersion` is refused;
* the previous `"unpublished"` sentinel is refused;
* an invented `privacyVersion` is refused;
* a false acceptance flag is refused;
* an omitted acceptance field is refused;
* what is **stored** is the server's own constant, not the request's string;
* a deployed environment refuses registration entirely while unpublished, and
  the refusal precedes every other check;
* local and test environments are exempt;
* existing users can still sign in, including in a deployed environment.

## 5. Live-activation blockers (unchanged by this milestone)

1. Solicitor review and approval of the Terms.
2. Final Terms version and `TERMS_PUBLISHED = True`.
3. ICO assessment and registration.
4. DPA and international-transfer work with Resend, Google and AliExpress.
5. Backups — there are none.
6. A real production frontend at the production hostname.
7. Production configuration and deployment, including migrations `0031`/`0032`.
8. Separately authorised live Google sign-in and real OTP email delivery.
