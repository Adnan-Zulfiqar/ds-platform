import Link from "next/link";

/**
 * The statutory company disclosures, in one place.
 *
 * Two separate obligations land on the same block of text, which is why it is a
 * component rather than prose repeated on each page:
 *
 * * **Regulation 25 of the Company, LLP and Business (Names and Trading
 *   Disclosures) Regulations 2015** requires a company to disclose, on its
 *   websites, the part of the United Kingdom in which it is registered, its
 *   registered number and the address of its registered office.
 * * **Regulation 6 of the Electronic Commerce (EC Directive) Regulations 2002**
 *   requires an information-society service provider to make available, easily
 *   directly and permanently, its name, the geographic address at which it is
 *   established, contact details allowing rapid and direct communication
 *   including an electronic mail address, and — where it is entered in a public
 *   register — the register and its registration number.
 *
 * Every value is the Companies House record for company 16381500, checked
 * against the register rather than typed from memory. The repeated "Eton Road"
 * in the registered office is **how Companies House holds it**, and correcting
 * it here would make the disclosure disagree with the register.
 *
 * No natural person is named. The contracting party is the company, and putting
 * a director's name on a contract page invites a reader to think otherwise.
 */

export const LEGAL_ENTITY = "DESIRLY LIMITED";
export const COMPANY_NUMBER = "16381500";
export const REGISTERED_OFFICE = "200 Eton Road Eton Road, Ilford, England, IG1 2UN";
export const LEGAL_CONTACT = "privacy@whiteto.com";
export const TRADING_NAME = "DropPilot AI";
/** The register the company number belongs to — ECR 2002 reg 6(1)(d). */
export const REGISTER = "the Companies House register for England and Wales";

export function CompanyDisclosure({ className }: { className?: string }) {
  return (
    <div className={className}>
      <dl className="space-y-1 text-sm text-muted-foreground">
        <div className="flex flex-wrap gap-x-2">
          <dt className="font-medium text-foreground">Legal entity</dt>
          <dd>
            {LEGAL_ENTITY}, a private limited company registered in England and Wales
          </dd>
        </div>
        <div className="flex flex-wrap gap-x-2">
          <dt className="font-medium text-foreground">Company number</dt>
          <dd>{COMPANY_NUMBER}</dd>
        </div>
        <div className="flex flex-wrap gap-x-2">
          <dt className="font-medium text-foreground">Registered office</dt>
          <dd>{REGISTERED_OFFICE}</dd>
        </div>
        <div className="flex flex-wrap gap-x-2">
          <dt className="font-medium text-foreground">Trading name</dt>
          <dd>{TRADING_NAME}</dd>
        </div>
        <div className="flex flex-wrap gap-x-2">
          <dt className="font-medium text-foreground">Contact</dt>
          <dd>
            <a className="text-primary hover:underline" href={`mailto:${LEGAL_CONTACT}`}>
              {LEGAL_CONTACT}
            </a>
          </dd>
        </div>
      </dl>
    </div>
  );
}

/**
 * The legal navigation pair.
 *
 * Small, but it is the only route between the two published documents, and a
 * reader who reaches one should not have to guess the URL of the other.
 */
export function LegalNav({ current }: { current: "terms" | "privacy" }) {
  return (
    <nav aria-label="Legal documents" className="flex flex-wrap gap-4 text-sm">
      <Link
        href="/terms"
        aria-current={current === "terms" ? "page" : undefined}
        className={
          current === "terms"
            ? "font-medium text-foreground"
            : "text-primary hover:underline"
        }
      >
        Terms of Service
      </Link>
      <Link
        href="/privacy"
        aria-current={current === "privacy" ? "page" : undefined}
        className={
          current === "privacy"
            ? "font-medium text-foreground"
            : "text-primary hover:underline"
        }
      >
        Privacy Notice
      </Link>
    </nav>
  );
}
