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

const LAST_UPDATED = "27 August 2026";
const CONTACT = "privacy@whiteto.com";

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
          <p className="text-sm font-medium text-muted-foreground">DropPilot AI</p>
          <h1 className="text-3xl font-bold tracking-tight sm:text-4xl">
            Privacy Policy
          </h1>
          <p className="text-sm text-muted-foreground">
            Last updated: <time dateTime="2026-08-27">{LAST_UPDATED}</time>
          </p>
        </header>

        <div className="mt-10 space-y-10">
          <Section id="scope" heading="1. Who this policy covers">
            <p>
              This policy explains how <strong>DropPilot AI</strong> (&ldquo;we&rdquo;,
              &ldquo;the service&rdquo;) handles personal data in the DropPilot AI
              dropshipping automation platform, including the web application and its
              API.
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
              Where UK or EU data protection law applies, we expect the following
              categories of lawful basis to be relevant:
            </p>
            <ul className="list-disc space-y-2 pl-5">
              <li>
                <strong>Performance of a contract</strong> — providing the service you
                signed up for.
              </li>
              <li>
                <strong>Legitimate interests</strong> — keeping the service secure,
                preventing abuse and diagnosing faults.
              </li>
              <li>
                <strong>Legal obligation</strong> — meeting requirements such as
                responding to marketplace account deletion notifications.
              </li>
              <li>
                <strong>Consent</strong> — where we ask for it explicitly, such as
                authorising a marketplace connection.
              </li>
            </ul>
            <p>
              Which basis applies to a specific processing activity depends on
              circumstances we cannot determine from the software alone. If you need a
              definitive statement for your own compliance records, contact us at the
              address in section 17.
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
                <strong>Infrastructure</strong> — the hosting, database and cache
                services the deployment runs on.
              </li>
              <li>
                <strong>Legal requirements</strong> — where we are required by law to
                disclose information.
              </li>
            </ul>
            <p>
              At the time of writing, the service implements no analytics, advertising,
              tracking or third-party error-reporting integrations.
            </p>
          </Section>

          <Section id="transfers" heading="8. International transfers">
            <p>
              The marketplaces and any optional providers described in section 7 operate
              internationally, so connecting an account may involve your data being
              processed outside the United Kingdom or the European Economic Area under
              those providers&rsquo; own terms and safeguards.
            </p>
            <p>
              The hosting location of a particular DropPilot AI deployment is a
              deployment decision and is not fixed by the software. If you need to know
              where your workspace&rsquo;s data is stored, ask us at the address in
              section 17 and we will tell you.
            </p>
          </Section>

          <Section id="retention" heading="9. Retention and deletion">
            <ul className="list-disc space-y-2 pl-5">
              <li>
                <strong>Account and workspace data</strong> is retained while your
                account is open.
              </li>
              <li>
                <strong>Marketplace connections</strong> are retained until you
                disconnect them. Disconnecting eBay deletes the connection and its
                encrypted tokens permanently.
              </li>
              <li>
                <strong>Session-refresh tokens</strong> are stored only as hashes and
                expire automatically; signing out revokes them.
              </li>
              <li>
                <strong>Marketplace account deletion notifications</strong> trigger
                permanent deletion of the matching seller connection. The non-personal
                record that the notification was handled is retained as compliance
                evidence.
              </li>
              <li>
                <strong>Operational logs</strong> record the request method, path,
                response status, duration, requesting IP address, user agent and a
                request identifier. They do not record request bodies, credentials or
                tokens.
              </li>
            </ul>
            <p>
              We have not yet fixed a published retention period for every category of
              data. Where no specific period is stated, we keep information only for as
              long as it is needed for the purposes in section 5, or for as long as the
              law requires. You can ask us to delete your account and its data at any
              time using the contact address in section 17; deletion is currently handled
              on request rather than through a self-service control in the application.
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
              If you are in the United Kingdom or the European Economic Area, you have
              the right to:
            </p>
            <ul className="list-disc space-y-2 pl-5">
              <li>access the personal data we hold about you;</li>
              <li>have inaccurate data corrected;</li>
              <li>have your data erased;</li>
              <li>restrict how we process it;</li>
              <li>object to processing carried out on the basis of legitimate interests;</li>
              <li>
                receive the data you provided in a portable format, where that right
                applies.
              </li>
            </ul>
            <p>
              To exercise any of these, contact us at{" "}
              <a
                className="font-medium text-foreground underline underline-offset-4 hover:no-underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
                href={`mailto:${CONTACT}`}
              >
                {CONTACT}
              </a>
              . We may need to verify your identity before acting.
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
              <strong className="text-foreground">DropPilot AI</strong>
              <br />
              <a
                className="font-medium text-foreground underline underline-offset-4 hover:no-underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
                href={`mailto:${CONTACT}`}
              >
                {CONTACT}
              </a>
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
