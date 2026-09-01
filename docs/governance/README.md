# Data governance

Written for EBAY-C1.2, from the implementation rather than from a template.
Every factual claim in these documents cites the code or configuration that
makes it true, and anything the code cannot settle is listed as an open decision
rather than filled in with something plausible.

**None of this is legal advice, and none of it has had legal review.** It is an
engineer's account of what the software does, assembled so that whoever is
responsible for the legal position has accurate facts to work from.

| Document | What it covers |
|---|---|
| [PROCESSING_REGISTER.md](PROCESSING_REGISTER.md) | Every processing purpose, the data it touches, the proposed lawful basis, recipients and evidence |
| [RETENTION_AND_ERASURE.md](RETENTION_AND_ERASURE.md) | What is actually enforced in code |
| [DATA_SUBJECT_REQUESTS.md](DATA_SUBJECT_REQUESTS.md) | The runbook: scopes, verification, database guard, rollback, deadline |
| [LOG_RETENTION.md](LOG_RETENTION.md) | The log pruning authority, its limits, and why it currently refuses to run |
| [BACKUPS.md](BACKUPS.md) | The backup position — tooling exists and is proven on isolated databases; **no production backup has been taken** |
| [SUBPROCESSORS.md](SUBPROCESSORS.md) | Active recipients, their locations and transfer position |
| [ICO_REGISTRATION_GATE.md](ICO_REGISTRATION_GATE.md) | The registration gate that blocks public launch |

---

## The controller

| Field | Value |
|---|---|
| Legal entity | **DESIRLY LIMITED** |
| Company number | **16381500** |
| Trading name | DropPilot AI |
| Registered office | 200 Eton Road Eton Road, Ilford, England, IG1 2UN |
| Privacy contact | privacy@whiteto.com |
| Operational contact | Adnan Zulfiqar |
| Production location | United Kingdom |
| ICO registration | **Not yet registered** — see the gate document |

---

## Blockers before public launch

These are not opinions about best practice. Each one is either a legal
requirement or a claim the software currently cannot support.

| # | Blocker | Why it blocks | Owner |
|---|---|---|---|
| 1 | **ICO registration** not completed | A UK controller processing personal data electronically must complete the fee self-assessment and, where required, register and pay before processing at launch scale | DESIRLY LIMITED |
| 2 | **No production backup exists** | An outage or disk failure loses every customer's workspace. BACKUP-B1 built and proved the tooling on isolated databases; nothing has been run against production, no off-site copy exists, and no key has been generated. Service continuity before privacy | DESIRLY LIMITED |
| 3 | **No Data Processing Agreement** offered to merchants | Where a merchant syncs orders, DESIRLY LIMITED processes their customers' data on their instructions. UK GDPR Article 28 requires that in writing | Legal review |
| 4 | **Log persistence undecided** | Nothing is written to disk today, so nothing is retained — but also nothing is available for security investigation. Whichever way this goes, the privacy notice must match | DESIRLY LIMITED |
| 5 | **`app.whiteto.com` does not resolve** | The privacy URL, the OAuth return URL and the application itself all depend on it (carried over from EBAY-C1) | DESIRLY LIMITED |
| 6 | **No exact buyer erasure** | `orders` carries no buyer identifier, so a merchant's customer cannot be matched reliably. Shopper requests are referred to the merchant. Needs a stable identifier on `orders` | Engineering |
| 7 | **Google app is in Testing mode** | Only listed test users can sign in. Publishing requires Google's verification, which reviews the consent screen and the privacy policy URL | DESIRLY LIMITED |
| 8 | **No Resend DPA or API key** | The key does not exist yet, and no processing agreement has been signed with Resend | DESIRLY LIMITED |
| 9 | **Legal review of the public notice** | These documents are an engineering audit, not a legal opinion | Legal review |

Items 1–4 are new in EBAY-C1.2, item 6 in EBAY-C1.2-R1, and items 7–8 in AUTH-G1. Item 5 is unchanged from
[EBAY_C1_PUBLIC_PREREQUISITES.md](../ebay/EBAY_C1_PUBLIC_PREREQUISITES.md).
