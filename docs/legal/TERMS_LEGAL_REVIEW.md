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
7. The legal and privacy contact is `privacy@whiteto.com`.

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
2. **£100 liability floor.** Is a fixed floor for unpaid accounts the right
   approach, and is £100 defensible? Alternatives: a fixed sum for all accounts
   regardless of fees, or a "greater of fees or £X" formulation.
3. **Twelve-month cap measurement.** "Paid or owed … in the twelve months before
   the event giving rise to the claim." Should it instead be the twelve months
   before the *claim*, or the contract year in which the event occurred?
4. **Excluding loss of data.** §26 excludes loss or corruption of data. Given
   there are **no backups**, is that exclusion reasonable under UCTA s.3(2), or
   does the absence of any recovery capability make it more vulnerable, not less?
   This is the question we are least comfortable answering ourselves.
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

* That the Terms are approved, in force, or reviewed by a solicitor.
* That DESIRLY LIMITED is registered with the ICO.
* That a DPA or international transfer assessment exists.
* That any backup, uptime, certification or support commitment exists.
* That there is any affiliation with a marketplace.
* That a payment processor is configured.
