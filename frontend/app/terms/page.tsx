import type { Metadata } from "next";
import Link from "next/link";

import {
  COMPANY_NUMBER,
  CompanyDisclosure,
  LEGAL_CONTACT,
  LEGAL_ENTITY,
  LegalNav,
  REGISTERED_OFFICE,
  TRADING_NAME,
} from "@/components/legal/company-disclosure";
import { TERMS_PUBLISHED, TERMS_VERSION } from "@/lib/legal";

/**
 * Terms of Service — **a draft awaiting solicitor review.**
 *
 * A Server Component with no hooks, no data fetching and no client state, in
 * the same position in the route tree as `/privacy`: outside the `(app)` group,
 * so no `AuthProvider` mounts, no session bootstrap runs, and the page renders
 * with JavaScript disabled. A contract nobody can read without an account is
 * not a published contract.
 *
 * **Every commercial statement below was checked against the implementation at
 * `5aaa6ec`**, and the audit is recorded in
 * `docs/legal/TERMS_PRODUCT_AUDIT.md`. Where the product does not do a thing,
 * the wording says so rather than promising it: there is no billing system, no
 * backup, no data-export feature, no support SLA and no uptime commitment, so
 * none of those appears as a promise. Clauses that describe fees exist because
 * a subscription is offered commercially, and are written to bind only when
 * a plan is actually purchased.
 *
 * Nothing here is legal advice, and nothing here has been approved by a
 * solicitor. `docs/legal/TERMS_LEGAL_REVIEW.md` lists the questions a
 * solicitor has to answer before this can be published.
 */

const LAST_UPDATED = "31 August 2026";

export const metadata: Metadata = {
  title: "Terms of Service",
  description:
    "The business terms on which DESIRLY LIMITED provides the DropPilot AI dropshipping and ecommerce automation service.",
  /**
   * A draft is deliberately **not** indexable, unlike `/privacy`.
   *
   * The privacy notice overrides the application's `noindex` because it exists
   * to be found by people with no account. These Terms are not approved, so a
   * search result presenting them as this company's contract would be actively
   * misleading. This flips to `index: true` in the same change that sets
   * `TERMS_PUBLISHED`.
   */
  robots: { index: false, follow: false },
};

function Section({
  id,
  heading,
  children,
}: {
  id: string;
  heading: string;
  children: React.ReactNode;
}) {
  return (
    <section aria-labelledby={id} className="space-y-3">
      <h2 id={id} className="text-xl font-semibold tracking-tight">
        {heading}
      </h2>
      <div className="space-y-3 text-sm leading-relaxed text-muted-foreground">
        {children}
      </div>
    </section>
  );
}

export default function TermsPage() {
  return (
    <main className="mx-auto w-full max-w-3xl px-4 py-10 sm:px-6 lg:px-8">
      <article className="space-y-10">
        <header className="space-y-4">
          {!TERMS_PUBLISHED && (
            <p
              data-testid="terms-draft-banner"
              role="note"
              className="rounded-md border border-warning/40 bg-warning/10 px-4 py-3 text-sm font-medium text-foreground"
            >
              Draft — pending legal review. This document has not been approved
              by a solicitor and is not yet in force. It is published here for
              review only and does not form a contract with anyone.
            </p>
          )}

          <h1 className="text-3xl font-bold tracking-tight sm:text-4xl">
            Terms of Service
          </h1>
          <p className="text-sm text-muted-foreground">
            Version <span data-testid="terms-version">{TERMS_VERSION}</span> ·
            Last updated:{" "}
            <time dateTime="2026-08-31">{LAST_UPDATED}</time>
          </p>
          <LegalNav current="terms" />
        </header>

        <Section id="who-we-are" heading="1. Who you are contracting with">
          <p>
            {TRADING_NAME} is a service provided by {LEGAL_ENTITY}, a private
            limited company registered in England and Wales with company number{" "}
            {COMPANY_NUMBER} and registered office at {REGISTERED_OFFICE}.
          </p>
          <p>
            In these Terms, &ldquo;we&rdquo;, &ldquo;us&rdquo; and
            &ldquo;our&rdquo; mean {LEGAL_ENTITY}. &ldquo;You&rdquo; means the
            business that opens an account.
          </p>
          <CompanyDisclosure className="rounded-md border bg-muted/40 p-4" />
        </Section>

        <Section id="definitions" heading="2. Definitions">
          <ul className="list-disc space-y-2 pl-5">
            <li>
              <strong>Service</strong> — the {TRADING_NAME} web application and
              the features made available in your account from time to time.
            </li>
            <li>
              <strong>Workspace</strong> — the tenant account created when you
              register, and all data held within it.
            </li>
            <li>
              <strong>Customer Content</strong> — everything you or your
              authorised users put into the Service, or that the Service
              retrieves on your instruction from a connected store or
              marketplace.
            </li>
            <li>
              <strong>Connected Service</strong> — a third-party store,
              marketplace, supplier or other platform you choose to connect,
              such as Shopify, eBay or AliExpress.
            </li>
            <li>
              <strong>Plan</strong> — a subscription to the Service, where one
              is offered and purchased.
            </li>
            <li>
              <strong>Fees</strong> — the amounts payable for a Plan.
            </li>
          </ul>
        </Section>

        <Section id="business-only" heading="3. Business customers only">
          <p>
            The Service is offered <strong>only to businesses</strong> and
            professional online sellers, for purposes related to their trade,
            business, craft or profession. It is not offered to consumers, and
            it is not intended for personal or household use.
          </p>
          <p>
            If you accept these Terms on behalf of a business, you confirm that
            you are <strong>at least 18 years old</strong> and that you are
            authorised to bind that business. If you do not have that authority,
            you must not open an account.
          </p>
          <p>
            Because you are contracting as a business, consumer protection law
            does not apply to this contract, and the statutory cancellation
            rights available to consumers do not apply.
          </p>
        </Section>

        <Section id="formation" heading="4. How the contract is formed">
          <p>
            A contract is formed when you complete registration and we create
            your workspace. During registration you are asked to confirm that
            you accept these Terms and have read our{" "}
            <Link href="/privacy" className="text-primary hover:underline">
              Privacy Notice
            </Link>
            . That confirmation is not pre-selected, and we record which version
            of each document you accepted and when.
          </p>
          <p>
            The version identifier shown at the top of this page is the
            authoritative one. It is held by our server, not by your browser: an
            acceptance quoting any other version is refused.
          </p>
        </Section>

        <Section id="accounts" heading="5. Accounts, credentials and security">
          <p>
            You may sign in with a password or with a Google account. You are
            responsible for your credentials, for every user you allow into your
            workspace and for everything done through your account.
          </p>
          <p>
            Tell us at{" "}
            <a className="text-primary hover:underline" href={`mailto:${LEGAL_CONTACT}`}>
              {LEGAL_CONTACT}
            </a>{" "}
            promptly if you believe your account has been accessed without your
            authorisation.
          </p>
          <p>
            Changing how an account can be signed in to — connecting or
            disconnecting a Google account — requires your password again, even
            when you are already signed in.
          </p>
        </Section>

        <Section id="service" heading="6. The Service and how you may use it">
          <p>
            The Service helps you import product data, build and edit product
            drafts, apply pricing and shipping rules, publish to connected
            stores, and track orders and inventory. Features change over time
            and are those actually present in your account.
          </p>
          <p>
            We grant you a non-exclusive, non-transferable right to use the
            Service for your own business while this contract is in force.
          </p>
        </Section>

        <Section id="integrations" heading="7. Connected stores and marketplaces">
          <p>
            You may connect third-party services. When you do, you authorise us
            to access and exchange data with them on your behalf, using the
            permissions you grant.
          </p>
          <p>
            Connected Services are operated by other companies under their own
            terms. We do not control them, and we are not responsible for their
            availability, their decisions, their policy changes or their
            enforcement action against your account with them.
          </p>
        </Section>

        <Section id="no-affiliation" heading="8. No affiliation with third parties">
          <p>
            {LEGAL_ENTITY} is <strong>not affiliated with, endorsed by,
            sponsored by or partnered with</strong> Shopify, eBay, AliExpress,
            Alibaba, TikTok, Google or any other platform, except where we state
            an official partnership in writing. Their names and marks are used
            only to identify the services you can connect, and belong to their
            respective owners.
          </p>
        </Section>

        <Section id="your-responsibilities" heading="9. What you are responsible for">
          <p>
            You sell to your own customers. We provide tooling; the trading
            decisions and the legal obligations that follow from them are yours.
            You are responsible for:
          </p>
          <ul className="list-disc space-y-1 pl-5">
            <li>complying with the rules of every marketplace and store you use;</li>
            <li>
              the legality, safety, labelling and compliance of the products you
              list and sell;
            </li>
            <li>
              holding the intellectual-property rights, or the permission, for
              every image, description, brand and mark you publish;
            </li>
            <li>the prices you set and the accuracy of your listings;</li>
            <li>
              all taxes, VAT, duties and customs obligations arising from your
              sales;
            </li>
            <li>
              customer service, returns, refunds and consumer-law obligations
              owed to your own customers;
            </li>
            <li>fulfilment, dispatch and the conduct of your suppliers.</li>
          </ul>
          <p>
            Automation does not transfer any of these to us. A rule that
            publishes a listing is your act, made through our tooling.
          </p>
        </Section>

        <Section id="data-roles" heading="10. Customer data and roles">
          <p>
            You control the data in your workspace. Where that data includes
            personal data — your customers&rsquo; details, for example — you act
            as the controller and we act as your processor, following your
            instructions given through the Service.
          </p>
          <p>
            You confirm that you have the lawful authority and any necessary
            consent or notice to put that data into the Service and to have us
            process it. Our{" "}
            <Link href="/privacy" className="text-primary hover:underline">
              Privacy Notice
            </Link>{" "}
            explains what we do with personal data and who it is shared with.
          </p>
          <p>
            We have not yet signed a data processing agreement with you. If you
            require one, contact{" "}
            <a className="text-primary hover:underline" href={`mailto:${LEGAL_CONTACT}`}>
              {LEGAL_CONTACT}
            </a>
            .
          </p>
        </Section>

        <Section id="plans" heading="11. Plans, renewal and tax">
          <p>
            Where a Plan is offered, it may be billed monthly or annually and
            renews automatically for further periods of the same length until
            cancelled. The Fees, the billing period and anything included are
            those shown to you at the point of purchase.
          </p>
          <p>
            Fees are exclusive of VAT and any other applicable tax, which is
            added where it applies.
          </p>
          <p>
            <strong>No paid Plan is on sale at the date of this version.</strong>{" "}
            This section governs Plans if and when they are offered, and creates
            no obligation to pay anything until you buy one.
          </p>
        </Section>

        <Section id="non-payment" heading="12. Failed payment">
          <p>
            If a payment for a Plan fails or is not made when due, we may
            suspend or limit access to the Service until it is paid. We will
            tell you before doing so where it is reasonably practicable.
          </p>
        </Section>

        <Section id="cancellation" heading="13. Cancelling">
          <p>
            You may cancel at any time. Cancellation takes effect at the end of
            the period you have already paid for, and you keep access until
            then.
          </p>
          <p>
            Cancellation is currently handled by contacting{" "}
            <a className="text-primary hover:underline" href={`mailto:${LEGAL_CONTACT}`}>
              {LEGAL_CONTACT}
            </a>
            . There is no self-service cancellation button in the application
            yet.
          </p>
        </Section>

        <Section id="refunds" heading="14. Refunds">
          <p>
            Fees are not refunded in part when you cancel partway through a
            billing period, and Plans are not refunded pro rata, except where we
            expressly agree otherwise in writing or where a refund is required
            by law.
          </p>
        </Section>

        <Section id="trials" heading="15. Trials and promotions">
          <p>
            We do not currently offer a free trial or promotional pricing. If we
            offer one, the terms shown at the point of sign-up — its length,
            what happens at the end and any conditions — apply in addition to
            these Terms.
          </p>
        </Section>

        <Section id="acceptable-use" heading="16. Acceptable use">
          <p>You must not use the Service to:</p>
          <ul className="list-disc space-y-1 pl-5">
            <li>do anything unlawful, fraudulent or deceptive;</li>
            <li>
              deal in counterfeit, stolen, restricted or otherwise prohibited
              goods;
            </li>
            <li>infringe anyone&rsquo;s intellectual-property rights;</li>
            <li>
              scrape, crawl or access any system, including ours, without
              authorisation;
            </li>
            <li>
              distribute malware, probe for vulnerabilities without our written
              permission, or interfere with the Service&rsquo;s operation;
            </li>
            <li>
              automate activity that breaks the terms of a marketplace, store or
              supplier;
            </li>
            <li>
              circumvent rate limits, usage limits, access controls, or the
              separation between one workspace and another;
            </li>
            <li>resell or make the Service available to anyone outside your business.</li>
          </ul>
        </Section>

        <Section id="content" heading="17. Your content">
          <p>
            Customer Content remains yours. You grant us a non-exclusive,
            worldwide, royalty-free licence to host, copy, transmit, display and
            adapt it strictly to the extent needed to operate and support the
            Service for you — including sending it to a Connected Service when
            you tell us to publish. That licence lasts only as long as we hold
            the content.
          </p>
        </Section>

        <Section id="our-ip" heading="18. Our intellectual property">
          <p>
            The Service, its software, interfaces, documentation and branding
            belong to us or our licensors. Nothing in these Terms transfers any
            of it to you. You may not copy, decompile or reverse-engineer the
            Service except to the extent the law says you may.
          </p>
        </Section>

        <Section id="confidentiality" heading="19. Confidentiality">
          <p>
            Each of us may receive information the other treats as confidential.
            Neither of us will use it except to perform this contract, or
            disclose it except to people who need it and are under similar
            obligations, or where the law requires disclosure. This does not
            apply to information that is public through no breach, already
            known, or independently developed.
          </p>
        </Section>

        <Section id="third-party-services" heading="20. Third-party services and outages">
          <p>
            The Service depends on third parties, including hosting, network,
            email and the marketplaces you connect. Their APIs change, rate-limit
            and fail, and when they do the Service may be affected. We are not
            liable for a Connected Service&rsquo;s acts, omissions, downtime,
            data or policy decisions.
          </p>
        </Section>

        <Section id="availability" heading="21. Changes, maintenance and availability">
          <p>
            We may change, add to or remove features. Where a change materially
            reduces what the Service does, we will give you reasonable notice.
          </p>
          <p>
            <strong>
              We do not offer a service-level agreement and we do not commit to
              any level of uptime.
            </strong>{" "}
            The Service may be unavailable for maintenance, for upgrades or
            because something has gone wrong.
          </p>
          <p>
            <strong>We do not currently operate a backup service.</strong> You
            should keep your own copies of anything you cannot afford to lose.
            We say this plainly because it would be easy to assume otherwise.
          </p>
        </Section>

        <Section id="suspension" heading="22. Suspension and termination">
          <p>
            We may suspend or terminate your access if you materially breach
            these Terms, if we are required to by law, if your use puts the
            Service or other customers at risk, or if Fees are unpaid. Where it
            is reasonable and lawful to do so, we will tell you first and give
            you an opportunity to put it right.
          </p>
          <p>
            You may stop using the Service at any time. Either of us may
            terminate for material breach that is not remedied within 30 days of
            written notice.
          </p>
        </Section>

        <Section id="closure" heading="23. Closure, export and deletion">
          <p>
            When your account is closed, we stop providing the Service. Data is
            retained and deleted in line with our{" "}
            <Link href="/privacy" className="text-primary hover:underline">
              Privacy Notice
            </Link>
            .
          </p>
          <p>
            <strong>
              There is no self-service data export in the application.
            </strong>{" "}
            If you need a copy of your data, or deletion, ask us at{" "}
            <a className="text-primary hover:underline" href={`mailto:${LEGAL_CONTACT}`}>
              {LEGAL_CONTACT}
            </a>{" "}
            and we will handle it manually. Export anything you need from your
            connected stores and marketplaces directly as well — they hold their
            own copies and we do not control them.
          </p>
        </Section>

        <Section id="no-results" heading="24. No guarantee of commercial results">
          <p>
            We do not guarantee sales, revenue, profit, margin, search ranking,
            listing approval, account standing on any marketplace, or continued
            access to any Connected Service. Marketplaces set their own rules
            and enforce them against you, not us. Nothing in the Service is
            business, tax, legal or investment advice.
          </p>
          <p>
            Where the Service produces suggested text, pricing or other output
            automatically, it is a suggestion. You are responsible for checking
            it before you publish it.
          </p>
        </Section>

        <Section id="warranties" heading="25. Warranties and disclaimers">
          <p>
            We will provide the Service with reasonable care and skill.
          </p>
          <p>
            Beyond that, and to the fullest extent the law allows, the Service
            is provided &ldquo;as is&rdquo;. We do not warrant that it will be
            uninterrupted, error-free, or that it will meet any particular
            requirement, and all terms implied by statute or common law are
            excluded to the extent permitted.
          </p>
        </Section>

        <Section id="liability" heading="26. Liability">
          <p className="font-medium text-foreground">
            Nothing in these Terms limits or excludes our liability for:
          </p>
          <ul className="list-disc space-y-1 pl-5">
            <li>death or personal injury caused by our negligence;</li>
            <li>fraud or fraudulent misrepresentation;</li>
            <li>
              anything else that cannot lawfully be limited or excluded.
            </li>
          </ul>
          <p>
            Subject to that, and to the extent permitted by law and so far as it
            is reasonable:
          </p>
          <ul className="list-disc space-y-1 pl-5">
            <li>
              neither of us is liable for indirect or consequential loss;
            </li>
            <li>
              we are not liable for loss of profit, revenue, business,
              anticipated savings, goodwill, or for loss or corruption of data,
              in each case whether direct or indirect;
            </li>
            <li>
              we are not liable for loss arising from a Connected Service, from
              a marketplace&rsquo;s decision about your account, or from a
              listing, price or order that you or your rules produced.
            </li>
          </ul>
          <p>
            <strong>
              Our total liability arising out of or in connection with this
              contract, whether in contract, tort (including negligence), breach
              of statutory duty or otherwise, is limited in aggregate to the
              Fees you paid or owed for the Service in the twelve months before
              the event giving rise to the claim.
            </strong>
          </p>
          <p>
            If you have paid no Fees — because no Plan was purchased, or the
            Service was provided without charge — our total liability is limited
            in aggregate to £100.
          </p>
          <p>
            These limits reflect that the Fees are set on the basis that
            liability is limited, that you are a business, that you can insure
            against trading losses, and that we do not control the marketplaces
            you sell on.
          </p>
        </Section>

        <Section id="indemnity" heading="27. Your indemnity">
          <p>
            You will indemnify us against losses, damages and reasonable costs
            we actually incur from a third-party claim arising out of:
          </p>
          <ul className="list-disc space-y-1 pl-5">
            <li>Customer Content, including any claim that it infringes a right;</li>
            <li>your unlawful or fraudulent use of the Service; or</li>
            <li>your material breach of these Terms.</li>
          </ul>
          <p>
            This applies only where we notify you of the claim promptly, let you
            take conduct of its defence, and do not settle it without your
            agreement. It does not apply to the extent the claim results from
            our own breach or negligence.
          </p>
        </Section>

        <Section id="force-majeure" heading="28. Events outside our control">
          <p>
            Neither of us is liable for failing to perform because of something
            beyond our reasonable control, including power, network or hosting
            failure, the failure or withdrawal of a third-party service, cyber
            attack, industrial action, epidemic, war or act of government. This
            does not excuse payment of Fees already due.
          </p>
        </Section>

        <Section id="changes" heading="29. Changes to these Terms">
          <p>
            We may change these Terms. Where a change materially affects your
            rights or obligations we will give you reasonable notice —
            ordinarily at least 30 days — by email or in the application. If you
            do not accept the change, you may cancel before it takes effect.
            Continuing to use the Service afterwards means you accept it.
          </p>
          <p>
            Each version carries an identifier. We record which version you
            accepted.
          </p>
        </Section>

        <Section id="notices" heading="30. Notices and electronic communication">
          <p>
            You agree to receive contractual notices electronically. We will
            write to the email address on your account or show the notice in the
            application. You should write to us at{" "}
            <a className="text-primary hover:underline" href={`mailto:${LEGAL_CONTACT}`}>
              {LEGAL_CONTACT}
            </a>{" "}
            or to our registered office.
          </p>
        </Section>

        <Section id="general" heading="31. General">
          <p>
            You may not assign or transfer this contract without our written
            consent. We may assign it to a group company or to a buyer of the
            business.
          </p>
          <p>
            If any provision is found unenforceable, the rest continues in
            force. A delay in enforcing a right does not waive it.
          </p>
          <p>
            These Terms and the documents they refer to are the entire agreement
            between us about the Service, and replace any earlier understanding.
            Nothing in this paragraph limits liability for fraudulent
            misrepresentation.
          </p>
        </Section>

        <Section id="third-party-rights" heading="32. Third-party rights">
          <p>
            A person who is not a party to this contract has no right under the
            Contracts (Rights of Third Parties) Act 1999 to enforce any of its
            terms.
          </p>
        </Section>

        <Section id="law" heading="33. Governing law and jurisdiction">
          <p>
            This contract, and any dispute arising out of or in connection with
            it, is governed by the law of England and Wales. The courts of
            England and Wales have exclusive jurisdiction.
          </p>
        </Section>

        <Section id="complaints" heading="34. Complaints and contact">
          <p>
            If something has gone wrong, write to{" "}
            <a className="text-primary hover:underline" href={`mailto:${LEGAL_CONTACT}`}>
              {LEGAL_CONTACT}
            </a>{" "}
            with your workspace name and what happened. We will acknowledge your
            message and work with you to resolve it. We do not publish a
            guaranteed response time, and we would rather say so than promise
            one we do not yet operate.
          </p>
          <CompanyDisclosure className="rounded-md border bg-muted/40 p-4" />
        </Section>

        <footer className="space-y-3 border-t pt-6">
          <LegalNav current="terms" />
          <p className="text-xs text-muted-foreground">
            {LEGAL_ENTITY} · Registered in England and Wales, company number{" "}
            {COMPANY_NUMBER} · Registered office: {REGISTERED_OFFICE}
          </p>
        </footer>
      </article>
    </main>
  );
}
