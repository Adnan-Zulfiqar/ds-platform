# Terms of Service — drafting assumptions, sources and open questions

**This document is not legal advice, and neither is `/terms`.** No solicitor has
reviewed either. The Terms are a commercial draft prepared from the operator's
stated business position and the primary sources listed below, for a solicitor
to review, correct and approve.

`TERMS_PUBLISHED` is `False` and this milestone deliberately does not change it.

---

## 1. Official sources consulted

Each was read at drafting time. Where a clause depends on one, the clause names
it in section 3.

| Source | Retrieved from | What it settled |
|---|---|---|
| Companies House register, company 16381500 | `find-and-update.company-information.service.gov.uk/company/16381500` | Registered name **DESIRLY LIMITED**, number **16381500**, status **Active**, type **Private limited Company**, incorporated **11 April 2025**, registered office **"200 Eton Road Eton Road, Ilford, England, IG1 2UN"**, SIC **47910 — Retail sale via mail order houses or via Internet** |
| The Company, Limited Liability Partnership and Business (Names and Trading Disclosures) Regulations 2015 (SI 2015/17), reg. 25 | `legislation.gov.uk/uksi/2015/17/regulation/25/made` | A company must disclose on its websites the part of the UK in which it is registered, its registered number and its registered office address |
| Electronic Commerce (EC Directive) Regulations 2002 (SI 2002/2013), reg. 6 | `legislation.gov.uk/uksi/2002/2013/regulation/6/made` | Name, geographic address of establishment, contact details including an email address allowing rapid and direct communication, and — where entered in a public register — the register and registration number, all "easily, directly and permanently accessible" |
| Unfair Contract Terms Act 1977, s. 2 | `legislation.gov.uk/ukpga/1977/50/section/2` | s.2(1): liability for **death or personal injury resulting from negligence cannot be excluded**. s.2(2): other negligence liability only so far as **reasonable** |
| Unfair Contract Terms Act 1977, s. 3 | `legislation.gov.uk/ukpga/1977/50/section/3` | Applies where one party deals on the other's **written standard terms of business** — which is exactly what these Terms are. s.3(2): a party cannot exclude liability for its own breach, or claim to render substantially different or no performance, except so far as **reasonable** |
| Misrepresentation Act 1967, s. 3 | `legislation.gov.uk/ukpga/1967/7/section/3` | A term excluding liability or remedy for pre-contract misrepresentation is of no effect except so far as it satisfies the UCTA s.11(1) reasonableness test |
| Contracts (Rights of Third Parties) Act 1999, s. 1 | `legislation.gov.uk/ukpga/1999/31/section/1` | A third party may enforce a term where the contract says so **or where the term purports to confer a benefit**, unless the parties did not intend it to be enforceable |

**Not consulted and not relied on:** UCTA s.11 and Schedule 2 were not fetched
in full. The reasonableness *test* is applied in the drafting rationale below
from the statutory language quoted in s.2(2), s.3(2) and Misrepresentation Act
s.3, but a solicitor should check the drafting against s.11 and Schedule 2
directly.

## 2. Assumptions taken from the operator

These were supplied as business facts, not derived from the code or the register.
If any is wrong the corresponding clause is wrong.

1. The service is offered **only** to businesses and professional sellers.
2. Plans, when offered, are monthly or annual recurring subscriptions.
3. Cancellation takes effect at the end of the paid term; access continues.
4. No prorated refunds, except by express agreement or where law requires.
5. The commercial liability position is an aggregate cap of the **previous
   twelve months' fees**.
6. Governing law and jurisdiction: **England and Wales**.
7. Contacts: `support@whiteto.com` for support, cancellation, contractual
   notices and complaints; `privacy@whiteto.com` for privacy rights, data
   protection and data-subject requests.

## 2a. Operator decisions recorded at LEGAL-T1-R1

These were **explicitly approved by the operator** after reading the LEGAL-T1
draft. They are commercial decisions by the party that will be bound by them.
None of them is legal approval, and none of them is a substitute for the
solicitor review in section 4.

| # | Decision | Status |
|---|---|---|
| 1 | **Zero-fee liability floor of £100** | **Operator-approved.** This was a Claude drafting proposal in LEGAL-T1 and is no longer one. Question 2 in section 4 is answered as to the *figure*; whether a fixed floor is the right legal mechanism, and whether £100 is defensible under UCTA s.11, remains for the solicitor |
| 2 | **`support@whiteto.com`** for general support, subscription cancellation, contractual notices, complaints and ordinary service enquiries | Operator-approved. Applied to `/terms` and the shared disclosure block |
| 3 | **`privacy@whiteto.com`** for privacy rights, data-protection matters, data-subject requests and privacy complaints only | Operator-approved. The Privacy Notice keeps this address and was not changed |
| 4 | **The loss-of-data exclusion may remain in the draft** | Operator-approved **conditionally** — see section 2b |

### Mailbox status

**Neither mailbox has been tested by this work.** No mail was sent, no DNS
record was changed and no provider setting was touched. `support@whiteto.com`
being created and verified for inbound delivery is a mandatory item on the
publication checklist. A Terms document that directs cancellation and
contractual notices to an address nobody reads is worse than one that gives no
address at all, because the reader believes they have served notice.

## 2b. The loss-of-data exclusion — approved, conditionally

Section 26 excludes liability for loss or corruption of data. The operator has
approved keeping that wording **in the draft**, on the express condition that
publication remains blocked until all three of the following are true:

1. **Backups exist.** `docs/governance/BACKUPS.md` currently records that there
   are none — "not 'untested', not 'informal' — none."
2. **Backup restoration has been tested.** Existing is not the same as working;
   an untested backup is a belief, not a capability.
3. **A solicitor has reviewed the exclusion and the liability clause as a
   whole.**

The reasoning behind the condition is the one recorded as question 4 in
section 4, and it has not gone away: excluding liability for loss of data while
operating no means of recovering it is the part of this contract most exposed
under the UCTA s.3(2) reasonableness test. The operator's decision is to keep
the clause and keep the blocker, not to keep the clause and drop the blocker.

**This approval does not weaken the backup blocker.** It is listed unchanged in
the publication checklist and in the live-activation blockers.

## 3. Drafting decisions and why

**B2B framing carries the whole document.** Consumer contracts are governed by
the Consumer Rights Act 2015, under which most of the exclusions here would be
unenforceable and several would be blacklisted outright. UCTA s.2(4) and s.3(3)
now both say in terms that they do not apply to consumer contracts. So §3 states
the business-only position expressly, and every later clause is written on that
footing. **If the service is in fact sold to consumers, this document is the
wrong document** — not a document with a bad clause.

**Nothing excludes what cannot be excluded.** §26 opens with the carve-outs
rather than burying them: death or personal injury from negligence (UCTA
s.2(1)), fraud and fraudulent misrepresentation, and a catch-all for anything
else non-excludable. The entire-agreement clause in §31 carries the same
fraud reservation, because an entire-agreement clause that swept up
misrepresentation would be caught by Misrepresentation Act s.3.

**The cap has a floor for unpaid users.** A cap of "fees paid in the last twelve
months" is £0 for anybody on a free or unbilled account, and a cap of zero is a
total exclusion by arithmetic — which is exactly the kind of term a court
examines for reasonableness under UCTA s.3(2). §26 therefore sets a £100
fallback. The figure is a drafting judgement, not an instruction from the
operator, and is flagged below.

**Reasonableness is asserted, and its basis is stated.** §26 closes by giving
the reasons the limits are said to be reasonable — pricing set on that basis,
business counterparty, insurability, no control over the marketplaces. Under
UCTA the burden of proving reasonableness is on the party relying on the term,
so the reasons belong in the document.

**The indemnity is narrow and mutual in its conditions.** It covers only
customer content, unlawful use and material breach; it carves out our own
breach or negligence; and it is conditional on prompt notice, conduct of the
defence and no settlement without the indemnifier's agreement. A one-sided
unlimited indemnity in standard terms is the sort of clause that attracts UCTA
scrutiny by the back door.

**Third-party rights are excluded expressly.** Under s.1(1)(b) of the 1999 Act a
term that *purports to confer a benefit* on a third party is enforceable by them
unless the parties show a contrary intention. Several clauses here mention
marketplaces and suppliers; §32 makes the contrary intention explicit.

**No claim is made that the product does not support.** See
`TERMS_PRODUCT_AUDIT.md`. The absences are stated in the Terms rather than
omitted, on the basis that a merchant who assumes there are backups and
discovers there are none has been misled by silence.

## 4. Open questions for the solicitor

Numbered so they can be answered in order.

1. **Jurisdiction wording.** Companies House does not show a dedicated
   jurisdiction field for this company; the register shows an English registered
   office. The Terms say "registered in England and Wales", per the operator.
   Is that the correct statutory form for this registration?
2. **£100 liability floor.** The figure is now **operator-approved** (section
   2a) rather than a drafting proposal. Still for the solicitor: is a fixed
   floor for unpaid accounts the right *mechanism*, and is £100 defensible under
   UCTA s.11? Alternatives: a fixed sum for all accounts regardless of fees, or
   a "greater of fees or £X" formulation.
3. **Twelve-month cap measurement.** "Paid or owed … in the twelve months before
   the event giving rise to the claim." Should it instead be the twelve months
   before the *claim*, or the contract year in which the event occurred?
4. **Excluding loss of data.** §26 excludes loss or corruption of data. Given
   there are **no backups**, is that exclusion reasonable under UCTA s.3(2), or
   does the absence of any recovery capability make it more vulnerable, not less?
   This is the question we are least comfortable answering ourselves. The
   operator has approved keeping the clause in the draft on the express
   condition that publication stays blocked until backups exist, restoration is
   tested and this question is answered — see section 2b. **The condition is
   part of the approval, not a caveat on it.**
5. **Suspension for non-payment** is described in §12 although no billing exists.
   Is describing a mechanism that is not yet built acceptable, or should §11–§12
   be removed until billing ships?
6. **Processor terms.** §10 states we act as processor but there is no Article 28
   processing agreement. Should a DPA be incorporated by reference now, or does
   the absence need to be more prominent?
7. **B2B verification.** Nothing checks that a registrant is a business or that
   the individual is 18 or authorised. Is the contractual warranty in §3
   sufficient, or is a verification step needed?
8. **Entire agreement and pre-contract statements.** Does §31 need an express
   non-reliance statement, and would one survive Misrepresentation Act s.3?
9. **Force majeure and fees.** §28 preserves the obligation to pay fees already
   due. Should a prolonged force-majeure event give either party a termination
   right?
10. **Change-of-terms mechanism.** §29 uses 30 days' notice with continued use
    as acceptance. Is that adequate for a B2B standard-terms contract, or should
    material changes require positive re-acceptance?
11. **Which marketplaces may be named.** §8 names Shopify, eBay, AliExpress,
    TikTok and Google to disclaim affiliation. Do any of their developer or
    partner agreements restrict use of their marks even in a disclaimer?
12. **Insurance.** Is professional indemnity or cyber cover in place? The
    reasonableness argument in §26 mentions insurability of the *customer's*
    trading losses, not ours.

## 5. Standing statements this document does **not** make

* That the £100 floor, the contact split or the data-loss exclusion have been
  **solicitor**-approved. They are operator-approved commercial decisions.
* That either mailbox has been created, tested or is receiving mail.
* That the Terms are approved, in force, or reviewed by a solicitor.
* That DESIRLY LIMITED is registered with the ICO.
* That a DPA or international transfer assessment exists.
* That any backup, uptime, certification or support commitment exists.
* That there is any affiliation with a marketplace.
* That a payment processor is configured.
