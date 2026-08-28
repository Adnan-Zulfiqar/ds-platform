import type { Metadata } from "next";
import Link from "next/link";

/**
 * Public privacy policy.
 *
 * Deliberately a Server Component with no hooks, no data fetching and no
 * client-side state, so it renders statically and is reachable with no session.
 * eBay requires a working privacy-policy URL for the RuName, and a page that
 * needed a login would fail that check.
 *
 * **Every factual claim below was checked against the implementation** — the
 * models, the middleware, the cookie call site, the logging middleware, the
 * repository delete paths and the eBay deletion processor. Where the code does
 * not settle a question (hosting region, retention periods, lawful basis), the
 * wording says so rather than inventing a comforting answer. See
 * `docs/ebay/EBAY_C1_PUBLIC_PREREQUISITES.md` for the audit and the list of
 * decisions an operator still has to make before this is published.
 */

const LAST_UPDATED = "28 August 2026";
const CONTACT = "privacy@whiteto.com";
const CONTROLLER = "DESIRLY LIMITED";
const COMPANY_NUMBER = "16381500";
const REGISTERED_OFFICE = "200 Eton Road Eton Road, Ilford, England, IG1 2UN";

export const metadata: Metadata = {
  // The root layout appends " | DropPilot AI", so the operator name is already
  // there — repeating it here produced "Privacy Policy — DropPilot AI |
  // DropPilot AI" in the tab.
  title: "Privacy Policy",
  description:
    "How DropPilot AI collects, uses, shares and deletes personal data, including data from connected eBay, Shopify and AliExpress accounts.",
  /**
   * Deliberately overrides the root layout's `noindex`.
   *
   * That default is correct for the rest of the application — everything else
   * is behind authentication, and its comment says so. This page is the sole
   * exception: it is the one route that exists precisely to be read by people
   * with no account, and a published policy that instructs search engines to
   * ignore it is not meaningfully published. eBay fetches the URL directly and
   * is unaffected either way; a merchant looking for how their data is handled
   * is not.
   */
  robots: { index: true, follow: true },
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

export default function PrivacyPage() {
  return (
    <main className="min-h-screen bg-background text-foreground">
      <div className="mx-auto w-full max-w-3xl px-4 py-10 sm:px-6 sm:py-14">
        <header className="space-y-3 border-b border-border pb-8">
          <p className="text-sm font-medium text-muted-foreground">
            {CONTROLLER} &mdash; trading as DropPilot AI
          </p>
          <h1 className="text-3xl font-bold tracking-tight sm:text-4xl">
            Privacy Policy
          </h1>
          <p className="text-sm text-muted-foreground">
            Last updated: <time dateTime="2026-08-28">{LAST_UPDATED}</time>
          </p>
        </header>

        <div className="mt-10 space-y-10">
          <Section id="scope" heading="1. Who this policy covers">
            <p>
              <strong>{CONTROLLER}</strong>, a company registered in England and Wales
              (company number {COMPANY_NUMBER}) and trading as{" "}
              <strong>DropPilot AI</strong>, is the data controller for this service.
              Our registered office is {REGISTERED_OFFICE}. In this policy
              &ldquo;we&rdquo; means {CONTROLLER}.
            </p>
            <p>
              This policy explains how we handle personal data in the DropPilot AI
              dropshipping automation platform, including the web application and its
              API.
            </p>
            <p>
              <strong>Where we are the controller, and where we are not.</strong> We are
              the controller for your account, your workspace, authentication, security
              and the running of the service. Where you connect a sales channel and we
              synchronise your orders, the personal details of{" "}
              <em>your customers</em> remain yours: you decide why they are held and
              what happens to them, and we handle them on your instructions. If one of
              your customers wants their data erased, they should ask you, and we will
              act on your instruction.
            </p>
            <p>
              It covers three groups of people: the people who create and use a
              DropPilot AI workspace; the marketplace seller accounts those users
              connect; and, where a connected sales channel is synchronised, the buyers
              whose order details reach the service from that channel.
            </p>
            <p>
              It does not cover the marketplaces themselves. eBay, Shopify and
              AliExpress each handle your data under their own privacy policies, and
              connecting an account does not change that.
            </p>
          </Section>

          <Section id="collected" heading="2. Information we collect from you">
            <ul className="list-disc space-y-2 pl-5">
              <li>
                <strong>Account details</strong> — your email address, first and last
                name, and a hashed form of your password. Passwords are stored using
                Argon2id and are never stored or transmitted in a readable form.
              </li>
              <li>
                <strong>Workspace details</strong> — the workspace name, its URL slug,
                status, timezone and default currency.
              </li>
              <li>
                <strong>Sign-in and security records</strong> — the time you last signed
                in, whether your account is active and verified, your role assignments,
                and hashed identifiers for session-refresh and email-verification tokens.
                The raw tokens are never stored.
              </li>
              <li>
                <strong>Content you create</strong> — product drafts, pricing and
                shipping rules, store settings, and notifications raised for you inside
                the application.
              </li>
            </ul>
          </Section>

          <Section id="marketplaces" heading="3. Information we receive from connected marketplaces">
            <p>
              You choose whether to connect a marketplace. Nothing is received until you
              complete that marketplace&rsquo;s own authorisation screen.
            </p>
            <ul className="list-disc space-y-2 pl-5">
              <li>
                <strong>eBay</strong> — described in detail in section 4.
              </li>
              <li>
                <strong>Shopify</strong> — your shop domain and shop identifier, an
                encrypted access token, and the permission scopes you granted.
              </li>
              <li>
                <strong>AliExpress</strong> — an application key and encrypted
                credentials for the account you authorise.
              </li>
              <li>
                <strong>Orders, where order synchronisation is used</strong> — this can
                include a buyer or recipient name, a recipient phone number, and a
                delivery city, province, postal code and country, together with the
                items, amounts and shipment tracking for that order. This is personal
                data about your customers, and you remain responsible for the lawful
                basis on which you share it with us.
              </li>
            </ul>
          </Section>

          <Section id="ebay" heading="4. eBay seller data">
            <p>
              When you connect an eBay seller account, DropPilot AI stores the
              following, and nothing more:
            </p>
            <ul className="list-disc space-y-2 pl-5">
              <li>
                eBay&rsquo;s <strong>immutable user identifier</strong> for the seller
                account. We key on this rather than the display name because eBay lets
                sellers change their display name, and an identifier that can change is
                one we could not reliably erase later.
              </li>
              <li>
                The seller&rsquo;s <strong>display username</strong>, shown on the
                integrations page so you can see which account is connected.
              </li>
              <li>
                <strong>Connection metadata</strong> — the eBay marketplace, the account
                type, the permissions you granted, token expiry times, and the
                connection&rsquo;s status.
              </li>
              <li>
                <strong>Encrypted access and refresh tokens.</strong> These are
                encrypted before they are written to the database. No API response from
                DropPilot AI contains a token, an encrypted token, or eBay&rsquo;s
                immutable identifier.
              </li>
            </ul>
            <p>
              <strong>What this version does not do.</strong> The current eBay
              integration establishes an authorised connection and displays its status.
              It does not yet import listings, inventory or orders from eBay. Extending
              it to do so requires further development and a corresponding review of
              this policy before it is released.
            </p>
            <p>
              <strong>Disconnecting.</strong> Disconnecting eBay from the integrations
              page permanently deletes the stored connection, including the encrypted
              tokens. It is removed outright, not marked as hidden. The grant itself
              lives with eBay; to withdraw that as well, remove DropPilot AI from your
              eBay account settings.
            </p>
            <p>
              <strong>eBay account deletion.</strong> DropPilot AI subscribes to
              eBay&rsquo;s marketplace account deletion notifications. When eBay tells us
              a seller account has been closed, the matching connection and its
              encrypted credentials are permanently deleted, across every workspace that
              held them. We keep a record that the notification was received and acted
              on. That record contains no username, no user identifier and no payload —
              only eBay&rsquo;s notification reference, timestamps and the outcome — and
              it is retained as evidence that we met the obligation.
            </p>
          </Section>

          <Section id="purposes" heading="5. Why we process this information">
            <ul className="list-disc space-y-2 pl-5">
              <li>To create your account and workspace, and to sign you in securely.</li>
              <li>
                To provide the features you use: importing and editing product drafts,
                applying pricing and shipping rules, and connecting sales channels.
              </li>
              <li>
                To communicate with the marketplaces you have connected, on your behalf
                and within the permissions you granted.
              </li>
              <li>
                To keep the service secure and available — rate limiting, abuse
                prevention, and diagnosing faults.
              </li>
              <li>To meet legal and marketplace compliance obligations.</li>
            </ul>
          </Section>

          <Section id="lawful-basis" heading="6. Lawful bases">
            <p>
              Under UK data protection law we rely on the following bases:
            </p>
            <ul className="list-disc space-y-2 pl-5">
              <li>
                <strong>Performance of a contract</strong> — creating and running your
                account and workspace, keeping you signed in, connecting the sales
                channels you ask us to connect, and holding your catalogue and orders so
                the service works.
              </li>
              <li>
                <strong>Legitimate interests</strong> — keeping the service available
                and accounts unbreached: rate limiting, sign-in throttling, request
                logging and diagnosing faults. Our interest is running a service that is
                not trivially abused; we use the least data that achieves it.
              </li>
              <li>
                <strong>Consent</strong> — authorising a marketplace connection. You
                give this at eBay, Shopify or AliExpress, and you can withdraw it at any
                time (section 13). We do not use consent as a basis for anything else.
              </li>
              <li>
                <strong>Legal obligation</strong> — responding to a data protection
                rights request, and anything else the law specifically requires of us.
              </li>
            </ul>
            <p>
              Where you connect a sales channel and we hold your customers&rsquo; order
              details, we act on your instructions rather than on a basis of our own —
              the basis for that processing is the one you rely on with your customer.
            </p>
          </Section>

          <Section id="sharing" heading="7. Who we share information with">
            <p>
              We do not sell personal data, and we do not share it for advertising.
            </p>
            <p>Data is shared only in these circumstances:</p>
            <ul className="list-disc space-y-2 pl-5">
              <li>
                <strong>Marketplaces you connect</strong> — eBay, Shopify and AliExpress
                receive requests we make on your behalf, using the permissions you
                granted. Nothing is sent to a marketplace you have not connected.
              </li>
              <li>
                <strong>Optional AI providers</strong> — the service can generate listing
                content. By default it uses a built-in placeholder generator that sends
                nothing anywhere. If an operator configures an external model provider,
                the text submitted for optimisation is sent to that provider.
              </li>
              <li>
                <strong>Optional exchange-rate provider</strong> — currency conversion is
                disabled by default. If an operator enables a live rates provider, only
                currency codes are exchanged; no personal data is sent.
              </li>
              <li>
                <strong>Cloudflare</strong> — our network provider. Every request to the
                service passes through Cloudflare, which handles the secure connection
                and sees request metadata including your IP address.
              </li>
              <li>
                <strong>Our own server</strong> — the application, database and cache
                run on a server we operate in the United Kingdom. No other hosting
                provider holds your data.
              </li>
              <li>
                <strong>Legal requirements</strong> — where we are required by law to
                disclose information.
              </li>
            </ul>
            <p>
              The AI and exchange-rate providers above are <strong>switched off</strong>
              {" "}today. We do not use an email provider, a payment processor, or any
              analytics, advertising, tracking or third-party error-reporting service —
              none is built into the application at all.
            </p>
          </Section>

          <Section id="transfers" heading="8. International transfers">
            <p>
              <strong>The service runs on a server in the United Kingdom.</strong> Your
              account, workspace, catalogue and order data are stored there.
            </p>
            <p>
              Two things reach outside that. Our network provider, Cloudflare, operates
              a global network and handles requests at whichever of its locations is
              nearest to you, which means request metadata including your IP address is
              processed outside the UK. And the marketplaces you choose to connect —
              eBay, Shopify and AliExpress — operate internationally and process data
              under their own terms as independent controllers.
            </p>
            <p>
              We have not yet completed our own transfer assessments for these
              providers, and we are not going to claim safeguards we have not put in
              place. If that matters to your own compliance position, ask us at the
              address in section 17 and we will tell you exactly where things stand
              rather than give you a form of words.
            </p>
          </Section>

          <Section id="retention" heading="9. Retention and deletion">
            <ul className="list-disc space-y-2 pl-5">
              <li>
                <strong>Account and workspace data</strong> is kept while your account
                is open, and erased when you ask us to close it.
              </li>
              <li>
                <strong>Sign-in sessions.</strong> The short-lived token your browser
                uses expires after 15 minutes. The longer-lived session token is stored
                only as a hash, expires after 30 days, is replaced every time it is
                used, and is revoked when you sign out.
              </li>
              <li>
                <strong>Email verification links</strong> expire after 24 hours.
              </li>
              <li>
                <strong>Marketplace connections</strong> are kept until you disconnect
                them. Disconnecting deletes the connection and its encrypted tokens
                outright — for eBay, Shopify and AliExpress alike.
              </li>
              <li>
                <strong>Security counters</strong> — rate limiting and sign-in
                throttling — expire automatically within minutes.
              </li>
              <li>
                <strong>Marketplace account deletion notifications</strong> trigger
                permanent deletion of the matching seller connection. We keep a record
                that the notification was handled, indefinitely, as evidence that we met
                the obligation. That record contains no name, no identifier and no
                payload.
              </li>
              <li>
                <strong>Operational logs</strong> record the request method, path,
                response status, duration, requesting IP address, user agent and a
                request identifier. They do not record request bodies, credentials or
                tokens. <strong>Our servers do not currently write these logs to
                disk</strong> — they exist only in the running process and are gone when
                it restarts. If we start storing them, we will keep them for no more
                than 30 days. Cloudflare, our network provider, keeps its own records
                under its own retention policy, which we do not control.
              </li>
            </ul>
            <p>
              <strong>Backups.</strong> We do not currently keep backup copies of the
              production database. Deleting data therefore removes it from the only copy
              we hold. We intend to introduce encrypted backups before we operate
              commercially, and we will update this policy when we do — including how
              deletion is applied to them.
            </p>
            <p>
              Where no specific period is given above, we keep information for as long
              as your account is open, or for as long as the law requires.
            </p>
          </Section>

          <Section id="security" heading="10. Security">
            <ul className="list-disc space-y-2 pl-5">
              <li>Passwords are hashed with Argon2id and are never stored in readable form.</li>
              <li>
                Marketplace credentials and tokens are encrypted before being written to
                the database.
              </li>
              <li>
                Session-refresh and email-verification tokens are stored as hashes, never
                as the original value.
              </li>
              <li>
                Each workspace&rsquo;s data is isolated at the data-access layer, so one
                workspace cannot read another&rsquo;s records.
              </li>
              <li>
                Notifications received from eBay are cryptographically verified before
                they are acted on.
              </li>
              <li>
                Traffic between your browser and the service is encrypted in transit.
              </li>
            </ul>
            <p>
              No online service can promise absolute security, and we do not. These
              measures reduce risk; they do not eliminate it.
            </p>
          </Section>

          <Section id="cookies" heading="11. Cookies and browser storage">
            <p>
              We use one cookie and one item of browser storage. Neither is used for
              advertising or tracking, and we set no third-party cookies.
            </p>
            <ul className="list-disc space-y-2 pl-5">
              <li>
                <strong>A session-refresh cookie</strong>, which keeps you signed in. It
                is marked HttpOnly so that page scripts cannot read it, is restricted to
                the authentication path, and is sent over HTTPS in deployed
                environments. It is strictly necessary for the service to work.
              </li>
              <li>
                <strong>One browser storage entry</strong>, remembering the last delivery
                country you selected, so you do not have to re-pick it. It stays in your
                browser and is never sent to us as personal data.
              </li>
            </ul>
            <p>
              The short-lived access token used to call the API is held in memory only.
              It is never written to cookies, local storage or session storage.
            </p>
          </Section>

          <Section id="rights" heading="12. Your rights">
            <p>
              Under UK data protection law you have the right to access a copy of your
              personal data, to have inaccurate data corrected, to have your data
              erased, to restrict or object to how we use it, and to receive it in a
              portable form.
            </p>
            <p>
              <strong>How to exercise them.</strong> Email{" "}
              <a
                className="font-medium text-foreground underline underline-offset-4 hover:no-underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
                href={`mailto:${CONTACT}`}
              >
                {CONTACT}
              </a>
              . We will acknowledge your request and complete it within one month, as
              the law requires. We may need to verify your identity first — usually by
              confirming you control the address on the account — because acting on an
              unverified request would expose your data to whoever asked.
            </p>
            <p>
              <strong>What erasure does.</strong> There is no self-service delete button
              in the application yet; we carry it out for you. We delete your sign-in
              credentials, your marketplace connections and their stored tokens, your
              notifications and your permission grants; we remove your name, email
              address and password from your account record and deactivate it; and we
              clear buyer and recipient details from your orders while keeping the
              commercial figures your business records depend on. We will tell you what
              was removed and what was kept.
            </p>
            <p>
              <strong>Automated decision-making.</strong> We do not make decisions about
              you by automated means that produce legal or similarly significant
              effects, and we do not profile you. The service automates work on your
              catalogue and prices at your instruction; it does not make decisions about
              people.
            </p>
          </Section>

          <Section id="consent" heading="13. Withdrawing consent">
            <p>
              Where we rely on your consent — most visibly when you authorise a
              marketplace connection — you can withdraw it at any time. Disconnect the
              marketplace from the integrations page, which deletes the stored
              credentials for that connection. You can also revoke the authorisation
              directly with the marketplace. Withdrawing consent does not affect
              processing carried out before you withdrew it.
            </p>
          </Section>

          <Section id="complaints" heading="14. Complaints">
            <p>
              If you are unhappy with how we handle your personal data, please contact us
              first at{" "}
              <a
                className="font-medium text-foreground underline underline-offset-4 hover:no-underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
                href={`mailto:${CONTACT}`}
              >
                {CONTACT}
              </a>
              .
            </p>
            <p>
              You also have the right to complain to the UK supervisory authority, the{" "}
              <a
                className="font-medium text-foreground underline underline-offset-4 hover:no-underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
                href="https://ico.org.uk/make-a-complaint/"
                rel="noreferrer noopener"
                target="_blank"
              >
                Information Commissioner&rsquo;s Office (ICO)
              </a>
              . If you are in the EEA, you may complain to your local supervisory
              authority instead.
            </p>
          </Section>

          <Section id="children" heading="15. Children">
            <p>
              DropPilot AI is a business tool for people running online retail
              operations. It is not directed at children, and we do not knowingly create
              accounts for anyone under 18. If you believe a child has provided us with
              personal data, contact us and we will delete it.
            </p>
          </Section>

          <Section id="changes" heading="16. Changes to this policy">
            <p>
              We may update this policy as the service changes — in particular as further
              marketplace capabilities are released. The &ldquo;last updated&rdquo; date
              at the top of this page always reflects the current version. Where a change
              materially affects how we use your data, we will tell account holders
              directly rather than relying on this page alone.
            </p>
          </Section>

          <Section id="contact" heading="17. Contact us">
            <p>
              For any question about this policy, or to exercise the rights in section
              12, contact:
            </p>
            <p>
              <strong className="text-foreground">{CONTROLLER}</strong>
              <br />
              trading as DropPilot AI
              <br />
              Registered in England and Wales, company number {COMPANY_NUMBER}
              <br />
              {REGISTERED_OFFICE}
              <br />
              <a
                className="font-medium text-foreground underline underline-offset-4 hover:no-underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
                href={`mailto:${CONTACT}`}
              >
                {CONTACT}
              </a>
            </p>
            <p>
              Privacy requests are handled by our privacy operations, overseen by our
              director Adnan Zulfiqar. {CONTROLLER} is the data controller; please
              address requests to the company at the email above rather than to an
              individual, so nothing is missed while someone is away.
            </p>
          </Section>
        </div>

        <footer className="mt-12 border-t border-border pt-6">
          <Link
            className="text-sm font-medium underline underline-offset-4 hover:no-underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
            href="/login"
          >
            Back to sign in
          </Link>
        </footer>
      </div>
    </main>
  );
}
