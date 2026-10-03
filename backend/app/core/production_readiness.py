"""One authority on what a deployed environment must satisfy.

**Why this module exists at all.** The deployed guards were spread through
`Settings.model_post_init` and raised on the *first* failure, which is right for
a process that must not start — and useless for an operator preparing a
deployment. They fix one setting, restart, discover the next, and repeat. There
was also no way to ask "would this configuration boot?" without booting it
against a live database.

So the rules live here as data, and there are two readers of them:

* `Settings.model_post_init` walks the startup rules and raises on the first
  failure, exactly as before, with exactly the same messages;
* `scripts/verify_production_config.py` walks *all* of them and reports every
  finding at once, without starting anything.

That is the point of the shared table: the CLI cannot say "ready" about a
configuration the application would refuse, and the application cannot refuse
something the CLI called ready, because there is only one list.

**Nothing here ever emits a value.** Findings carry a setting name, a status and
a sentence. A configuration validator that printed the thing it was validating
would be a new way to leak the secrets it exists to protect — into a terminal,
a CI log, a screenshot, a support ticket. Where a reason needs to describe a
value it describes a *property* of it: its length band, whether it equals
another setting, whether it matches a published default, whether a URL is
loopback.

**The reason contract: no `=`, anywhere, for any purpose.** `Finding` refuses to
be constructed with a reason containing an equals sign. That is deliberately
blunter than the problem it guards, and the bluntness is the point — a rule of
"no interpolated values" needs a human to judge every reason, and a rule of "no
`=`" needs nobody. It costs authors a little: `>=` must be written "at least",
`key=value` must be described rather than shown, and "set to true" replaces
"`X=true`". That is the whole price, and it is paid once per sentence.

The alternative was matching reasons against known secret values, which fails
exactly when it matters: it cannot recognise a secret it was not handed, and the
reason a value reaches a report is usually that nobody realised it was one.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from urllib.parse import urlsplit

__all__ = [
    "RULES",
    "Classification",
    "Finding",
    "ReadinessReport",
    "Rule",
    "Status",
    "evaluate",
    "startup_failure",
]


class Status(StrEnum):
    """What a rule concluded."""

    # ruff reads a constant literally named PASS as a possible password.
    PASS = "PASS"  # noqa: S105 - a verdict, not a credential
    #: The configuration is wrong and a deployed environment must not start.
    FAIL = "FAIL"
    #: The configuration is *correct*, and something outside it forbids going
    #: live anyway — the Terms being unpublished is the whole reason this state
    #: exists. Deliberately not `FAIL`: nobody should "fix" it by editing a file.
    BLOCKED = "BLOCKED"
    #: A dependency an enabled integration needs is absent.
    MISSING = "MISSING"
    #: The rule does not apply, because the integration it guards is switched
    #: off. An absent optional integration is not a defect, and calling it one
    #: trains operators to ignore the report.
    SKIPPED = "SKIPPED"


class Classification(StrEnum):
    """When a setting has to be right."""

    REQUIRED_NOW = "required-now"
    REQUIRED_AT_ACTIVATION = "required-at-activation"
    OPTIONAL = "optional"


@dataclass(frozen=True, slots=True)
class Finding:
    """One rule's conclusion. Carries no configuration value, ever."""

    setting: str
    status: Status
    reason: str
    classification: Classification = Classification.REQUIRED_NOW
    secret: bool = False

    def __post_init__(self) -> None:
        """Refuse any reason containing an equals sign.

        `KEY=value` is the shape an accidental f-string produces, and once a
        secret is in a CI log it is in the CI log — so the shape is banned
        outright rather than judged case by case.

        **This rejects innocent uses too, and that is intended.** `>=`, `<=`,
        `==`, a base64 value's trailing padding and a prose example like
        `SECURITY_COOKIE_SECURE=true` are all refused. Write "at least", "no
        more than", "equals", or name the setting and describe its state. The
        rule is worth keeping precisely because it needs no judgement: a
        reviewer can verify it by looking, and a future author cannot talk
        themselves past it.
        """
        if "=" in self.reason:
            raise ValueError(
                f"the reason for {self.setting} contains '=', which is the shape an "
                "interpolated configuration value takes. Describe the property in "
                "words — 'at least 32 characters', not a comparison operator; the "
                "setting's name and state, not a sample assignment."
            )


@dataclass(frozen=True, slots=True)
class Rule:
    """A single requirement, and how to decide whether it is met."""

    setting: str
    classification: Classification
    secret: bool
    owner: str
    requirement: str
    check: Callable[[Any], Finding]
    #: Whether `Settings.model_post_init` also enforces this on startup. Rules
    #: that are advisory for an operator — an activation dependency, say — are
    #: reported by the CLI but must not stop a process that is deliberately
    #: running before activation.
    enforced_at_startup: bool = False


# ---------------------------------------------------------------------------
# Helpers. None of these returns a configuration value.
# ---------------------------------------------------------------------------


def _is_loopback(host: str) -> bool:
    host = host.lower()
    # `0.0.0.0` is a bind address, never a reachable one — a browser sent there
    # goes nowhere. ruff reads the literal as a bind and is right about the
    # literal, wrong about the use.
    unreachable = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}  # noqa: S104 - compared, not bound
    return host in unreachable or host.endswith(".localhost")


def _url_problem(raw: str) -> str | None:
    """Describe why a browser-facing URL is unfit for production, or `None`."""
    if not raw.strip():
        return "is empty"
    parts = urlsplit(raw.strip())
    if not parts.scheme or not parts.netloc:
        return "is not an absolute URL with a scheme and host"
    host = parts.hostname or ""
    if _is_loopback(host):
        return "points at a loopback address, which no browser outside this machine can reach"
    if parts.scheme != "https":
        return "is not https, so credentials and session cookies would travel in clear text"
    return None


def _secret(value: Any) -> str:
    """Unwrap a `SecretStr` without ever returning it to a caller that prints."""
    return value.get_secret_value() if hasattr(value, "get_secret_value") else str(value or "")


# ---------------------------------------------------------------------------
# The rules
# ---------------------------------------------------------------------------


def _check_environment(settings: Any) -> Finding:
    if not settings.environment.is_deployed:
        return Finding(
            "ENVIRONMENT",
            Status.FAIL,
            f"is {settings.environment.value!r}, which is not a deployed environment. "
            "Every production guard in this application is conditional on "
            "`is_deployed`, so none of them is in force.",
        )
    return Finding(
        "ENVIRONMENT", Status.PASS, f"is {settings.environment.value!r}, a deployed environment."
    )


def _check_secret_key(settings: Any) -> Finding:
    from app.core.config import SecuritySettings

    value = _secret(settings.security.secret_key)
    if value == SecuritySettings.LOCAL_PLACEHOLDER_KEY:
        return Finding(
            "SECURITY_SECRET_KEY",
            Status.FAIL,
            "is still the local placeholder, which is published in this repository. "
            "Anyone who can read the source can mint access tokens.",
            secret=True,
        )
    if len(value) < 32:
        return Finding(
            "SECURITY_SECRET_KEY",
            Status.FAIL,
            "is shorter than the 32 characters RFC 7518 requires for HS256.",
            secret=True,
        )
    return Finding(
        "SECURITY_SECRET_KEY", Status.PASS, "is set, unique and long enough.", secret=True
    )


def _check_otp_key(settings: Any) -> Finding:
    from app.core.config import SecuritySettings

    value = _secret(settings.security.otp_hmac_key)
    if not value:
        return Finding(
            "SECURITY_OTP_HMAC_KEY",
            Status.FAIL,
            "is empty; reset codes would be keyed with nothing.",
            secret=True,
        )
    if value == SecuritySettings.INSECURE_OTP_KEY_DEFAULT:
        return Finding(
            "SECURITY_OTP_HMAC_KEY",
            Status.FAIL,
            "is the development default, which is published in this repository. "
            "Stored six-digit reset codes would be brute-forceable offline from a "
            "Redis snapshot.",
            secret=True,
        )
    if value == _secret(settings.security.secret_key):
        return Finding(
            "SECURITY_OTP_HMAC_KEY",
            Status.FAIL,
            "is the same value as SECURITY_SECRET_KEY. One leak would compromise "
            "both, and neither could be rotated without the other.",
            secret=True,
        )
    return Finding(
        "SECURITY_OTP_HMAC_KEY",
        Status.PASS,
        "is set, non-default and distinct from the signing key.",
        secret=True,
    )


def _check_encryption_keys(settings: Any) -> Finding:
    import base64

    from app.core.config import SecuritySettings

    keys = [_secret(k) for k in settings.security.encryption_keys]
    if not keys:
        return Finding(
            "SECURITY_ENCRYPTION_KEYS",
            Status.FAIL,
            "is empty; storing a supplier credential would fail at first use rather "
            "than at startup.",
            secret=True,
        )
    published = set(SecuritySettings.PUBLISHED_TEST_ENCRYPTION_KEYS)
    if any(k in published for k in keys):
        return Finding(
            "SECURITY_ENCRYPTION_KEYS",
            Status.FAIL,
            "contains a key published in this repository. Every stored credential "
            "would be readable by anyone with the source.",
            secret=True,
        )
    if _secret(settings.security.secret_key) in keys:
        return Finding(
            "SECURITY_ENCRYPTION_KEYS",
            Status.FAIL,
            "reuses the signing key. Signing keys rotate on suspicion; encryption "
            "keys cannot rotate until stored ciphertext is re-encrypted.",
            secret=True,
        )
    for key in keys:
        try:
            if len(base64.urlsafe_b64decode(key)) != 32:
                raise ValueError
        except Exception:  # any decode failure is the same defect
            return Finding(
                "SECURITY_ENCRYPTION_KEYS",
                Status.FAIL,
                "contains an entry that is not a urlsafe base64 32-byte Fernet key.",
                secret=True,
            )
    return Finding(
        "SECURITY_ENCRYPTION_KEYS",
        Status.PASS,
        f"holds {len(keys)} valid Fernet key(s), none of them published.",
        secret=True,
    )


def _check_cookie_secure(settings: Any) -> Finding:
    if not settings.security.cookie_secure:
        return Finding(
            "SECURITY_COOKIE_SECURE",
            Status.FAIL,
            "is false. The refresh cookie is the longest-lived credential a browser "
            "holds, and without Secure it travels over plain HTTP.",
        )
    return Finding("SECURITY_COOKIE_SECURE", Status.PASS, "is true.")


def _check_allowed_hosts(settings: Any) -> Finding:
    hosts = list(settings.allowed_hosts)
    if "*" in hosts:
        return Finding(
            "ALLOWED_HOSTS", Status.FAIL, "is a wildcard, which permits Host header attacks."
        )
    if not hosts:
        return Finding("ALLOWED_HOSTS", Status.FAIL, "is empty.")
    if all(_is_loopback(h) for h in hosts):
        return Finding("ALLOWED_HOSTS", Status.FAIL, "names only loopback hosts.")
    return Finding("ALLOWED_HOSTS", Status.PASS, f"names {len(hosts)} explicit host(s).")


def _check_cors(settings: Any) -> Finding:
    origins = [str(o) for o in settings.cors_origins]
    if not origins:
        return Finding(
            "CORS_ORIGINS",
            Status.FAIL,
            "is empty, so the browser application would be unable to call the API.",
        )
    problems = [o for o in origins if _url_problem(o)]
    if problems:
        return Finding(
            "CORS_ORIGINS",
            Status.FAIL,
            f"{len(problems)} of {len(origins)} origin(s) are loopback or not https; "
            "a production origin must be the public frontend over TLS.",
        )
    return Finding("CORS_ORIGINS", Status.PASS, f"names {len(origins)} public https origin(s).")


def _check_request_body_logging(settings: Any) -> Finding:
    if settings.observability.include_request_body:
        return Finding(
            "LOG_INCLUDE_REQUEST_BODY",
            Status.FAIL,
            "is true; request bodies carry customer data into the logs.",
        )
    return Finding("LOG_INCLUDE_REQUEST_BODY", Status.PASS, "is false.")


def _check_trusted_proxies(settings: Any) -> Finding:
    proxies = list(getattr(settings.security, "trusted_proxies", []) or [])
    if not proxies:
        return Finding(
            "SECURITY_TRUSTED_PROXIES",
            Status.MISSING,
            "is empty. Forwarding headers are then ignored and every request is "
            "attributed to the socket peer, so behind a tunnel or load balancer "
            "the per-address rate limits collapse into one shared bucket.",
            classification=Classification.REQUIRED_AT_ACTIVATION,
        )
    return Finding(
        "SECURITY_TRUSTED_PROXIES",
        Status.PASS,
        f"names {len(proxies)} CIDR(s), so forwarding headers are believed only from those peers.",
        classification=Classification.REQUIRED_AT_ACTIVATION,
    )


def _check_ebay_endpoint(settings: Any) -> Finding:
    endpoint = settings.ebay.marketplace_deletion_endpoint
    if not endpoint:
        return Finding(
            "EBAY_MARKETPLACE_DELETION_ENDPOINT",
            Status.SKIPPED,
            "is unset; eBay marketplace-account-deletion notifications are not configured.",
            classification=Classification.REQUIRED_AT_ACTIVATION,
        )
    problem = _url_problem(endpoint)
    if problem:
        return Finding(
            "EBAY_MARKETPLACE_DELETION_ENDPOINT",
            Status.FAIL,
            f"{problem}. eBay refuses to validate an endpoint it cannot reach over https.",
            classification=Classification.REQUIRED_AT_ACTIVATION,
        )
    return Finding(
        "EBAY_MARKETPLACE_DELETION_ENDPOINT",
        Status.PASS,
        "is a public https endpoint.",
        classification=Classification.REQUIRED_AT_ACTIVATION,
    )


def _check_ebay_seller_channel(settings: Any) -> Finding:
    """EBAY-C6: the seller channel (C1 to C5) is either off or fully configured.

    Off (no client id) is SKIPPED, not a defect. Half-configured is MISSING:
    connect would fail at eBay with nothing diagnosable. eBay also refuses a
    production keyset's calls until account-deletion compliance validates, so
    the verification token and endpoint are part of "configured" here.
    """
    ebay = settings.ebay
    if not ebay.client_id.strip():
        return Finding(
            "EBAY_SELLER_CHANNEL",
            Status.SKIPPED,
            "is off: no eBay application credentials are configured.",
            classification=Classification.REQUIRED_AT_ACTIVATION,
        )
    missing = [
        name
        for name, present in (
            ("EBAY_CLIENT_SECRET", bool(_secret(ebay.client_secret))),
            ("EBAY_REDIRECT_URI_NAME", bool(ebay.redirect_uri_name.strip())),
            (
                "EBAY_MARKETPLACE_DELETION_VERIFICATION_TOKEN",
                bool(_secret(ebay.marketplace_deletion_verification_token)),
            ),
            ("EBAY_MARKETPLACE_DELETION_ENDPOINT", bool(ebay.marketplace_deletion_endpoint)),
        )
        if not present
    ]
    if missing:
        return Finding(
            "EBAY_SELLER_CHANNEL",
            Status.MISSING,
            f"is partly configured; still needed: {', '.join(missing)}.",
            classification=Classification.REQUIRED_AT_ACTIVATION,
        )
    return Finding(
        "EBAY_SELLER_CHANNEL",
        Status.PASS,
        f"is configured for the eBay {ebay.environment.value} environment.",
        classification=Classification.REQUIRED_AT_ACTIVATION,
    )


def _check_frontend_api_url(settings: Any) -> Finding:
    """The origin the browser bundle was built against.

    Read from the process environment rather than `Settings`, because
    `NEXT_PUBLIC_*` belongs to the frontend build and is inlined at build time —
    setting it on the server afterwards does nothing. It is audited here because
    getting it wrong produces a production bundle that calls localhost.
    """
    import os

    value = os.environ.get("NEXT_PUBLIC_API_URL", "")
    if not value:
        return Finding(
            "NEXT_PUBLIC_API_URL",
            Status.MISSING,
            "is not set in the audited configuration. It is inlined into the "
            "frontend bundle at build time, so the value present when the bundle "
            "is built is the one that ships.",
            classification=Classification.REQUIRED_AT_ACTIVATION,
        )
    problem = _url_problem(value)
    if problem:
        return Finding(
            "NEXT_PUBLIC_API_URL",
            Status.FAIL,
            f"{problem}. Every browser would call that address, not the API.",
            classification=Classification.REQUIRED_AT_ACTIVATION,
        )
    return Finding(
        "NEXT_PUBLIC_API_URL",
        Status.PASS,
        "is a public https origin.",
        classification=Classification.REQUIRED_AT_ACTIVATION,
    )


def _check_return_urls(settings: Any) -> Iterator[Finding]:
    """Browser-facing OAuth return URLs. Loopback here strands a real merchant."""
    import os

    for name in (
        "SHOPIFY_FRONTEND_RETURN_URL",
        "ALIEXPRESS_FRONTEND_RETURN_URL",
        "EBAY_FRONTEND_RETURN_URL",
    ):
        value = os.environ.get(name, "")
        if not value:
            yield Finding(
                name,
                Status.MISSING,
                "is unset; a merchant completing OAuth would not be returned to the application.",
                classification=Classification.REQUIRED_AT_ACTIVATION,
            )
            continue
        problem = _url_problem(value)
        if problem:
            yield Finding(
                name,
                Status.FAIL,
                f"{problem}.",
                classification=Classification.REQUIRED_AT_ACTIVATION,
            )
        else:
            yield Finding(
                name,
                Status.PASS,
                "is a public https URL.",
                classification=Classification.REQUIRED_AT_ACTIVATION,
            )


def _check_google(settings: Any) -> Finding:
    client_id = settings.google_oauth.client_id
    if not client_id:
        return Finding(
            "GOOGLE_OAUTH_CLIENT_ID",
            Status.SKIPPED,
            "is unset, so Google sign-in is switched off and the button is not "
            "rendered. That is a valid pre-activation state, not a defect.",
            classification=Classification.REQUIRED_AT_ACTIVATION,
        )
    if len(client_id) < 10:
        return Finding(
            "GOOGLE_OAUTH_CLIENT_ID",
            Status.FAIL,
            "is set but too short to be a Google client id.",
            classification=Classification.REQUIRED_AT_ACTIVATION,
        )
    return Finding(
        "GOOGLE_OAUTH_CLIENT_ID",
        Status.PASS,
        "is set. The frontend must be built with the same value in NEXT_PUBLIC_GOOGLE_CLIENT_ID.",
        classification=Classification.REQUIRED_AT_ACTIVATION,
    )


def _check_email(settings: Any) -> Iterator[Finding]:
    """Three states, and they must not be confused.

    Disabled is fine. Enabled-and-complete is fine. **Enabled-and-incomplete**
    is the dangerous one: the first password reset fails, for somebody who is
    already locked out of their account.
    """
    provider = settings.email.provider
    if not settings.email.sends_real_email:
        yield Finding(
            "EMAIL_PROVIDER",
            Status.SKIPPED,
            f"is {provider!r}, so no real email is sent. Password-reset codes are "
            "generated but not delivered — a valid pre-activation state.",
            classification=Classification.REQUIRED_AT_ACTIVATION,
        )
        return

    yield Finding(
        "EMAIL_PROVIDER",
        Status.PASS,
        f"is {provider!r}; real email delivery is enabled.",
        classification=Classification.REQUIRED_AT_ACTIVATION,
    )
    key = settings.resend.api_key
    if key is None or not _secret(key):
        yield Finding(
            "RESEND_API_KEY",
            Status.MISSING,
            "is absent while delivery is enabled. The first password-reset send "
            "would fail, for a person who is already locked out.",
            classification=Classification.REQUIRED_AT_ACTIVATION,
            secret=True,
        )
    else:
        yield Finding(
            "RESEND_API_KEY",
            Status.PASS,
            "is set.",
            classification=Classification.REQUIRED_AT_ACTIVATION,
            secret=True,
        )

    sender = settings.email.from_address
    if not sender.strip():
        yield Finding(
            "EMAIL_FROM",
            Status.MISSING,
            "is empty while delivery is enabled.",
            classification=Classification.REQUIRED_AT_ACTIVATION,
        )
    elif "auth.whiteto.com" not in sender:
        yield Finding(
            "EMAIL_FROM",
            Status.FAIL,
            "does not use the verified sending domain auth.whiteto.com; mail from "
            "an unverified domain is rejected or filed as spam.",
            classification=Classification.REQUIRED_AT_ACTIVATION,
        )
    else:
        yield Finding(
            "EMAIL_FROM",
            Status.PASS,
            "uses the verified sending domain.",
            classification=Classification.REQUIRED_AT_ACTIVATION,
        )


def _check_database_tls(settings: Any) -> Finding:
    """A managed database is reached over a network this application does not own.

    On the old Windows host PostgreSQL was a loopback process and TLS was
    genuinely unnecessary. On Lightsail the database is a separate managed
    service on AWS's network, and `require` — encryption with no idea who
    answered — is not the same control as `verify-full`, which checks the chain
    and the hostname. The difference is exactly an attacker who can redirect
    traffic.
    """
    from pathlib import Path

    database = settings.database
    mode = database.sslmode

    if mode in ("disable", "allow", "prefer"):
        return Finding(
            "POSTGRES_SSLMODE",
            Status.FAIL,
            f"is {mode!r}, which permits an unencrypted connection to the "
            "database. A managed instance is reached across a network the "
            "application does not control.",
        )
    if mode == "require":
        return Finding(
            "POSTGRES_SSLMODE",
            Status.FAIL,
            "is 'require', which encrypts the connection but never checks who "
            "answered. Use 'verify-full' so the certificate chain and the "
            "hostname are both verified.",
        )
    if not database.sslrootcert:
        return Finding(
            "POSTGRES_SSLROOTCERT",
            Status.MISSING,
            f"is unset while POSTGRES_SSLMODE is {mode!r}. Verification would "
            "fall back to the host's trust store, so any publicly-trusted "
            "certificate for the hostname would be accepted. Point it at the "
            "regional AWS CA bundle.",
        )
    if not Path(database.sslrootcert).is_file():
        return Finding(
            "POSTGRES_SSLROOTCERT",
            Status.FAIL,
            "names a file that does not exist, so certificate verification "
            "cannot succeed and every connection will fail at startup.",
        )
    return Finding(
        "POSTGRES_SSLMODE",
        Status.PASS,
        f"is {mode!r} with a CA bundle present; the database connection is "
        "encrypted and the server is authenticated.",
    )


def _check_terms(settings: Any) -> Finding:
    """Not a configuration defect. A deliberate publication gate."""
    from app.core.legal import TERMS_PUBLISHED, TERMS_VERSION

    if not TERMS_PUBLISHED:
        return Finding(
            "TERMS_PUBLISHED",
            Status.BLOCKED,
            f"is false and TERMS_VERSION is {TERMS_VERSION!r}. A deployed environment "
            "will refuse every registration until the Terms are approved and "
            "published. This is intentional and must not be resolved by editing "
            "configuration.",
            classification=Classification.REQUIRED_AT_ACTIVATION,
        )
    return Finding(
        "TERMS_PUBLISHED",
        Status.PASS,
        f"is true; the published version is {TERMS_VERSION!r}.",
        classification=Classification.REQUIRED_AT_ACTIVATION,
    )


def _check_backups(settings: Any) -> Finding:
    """Report the operational evidence that exists, and never more than that.

    The tooling to take, verify and restore an encrypted backup exists as of
    BACKUP-B1 and has been proven on isolated databases. **That proves the
    tooling, not the regime.** A rule that turned `PASS` the moment the code
    was merged would be asserting something nobody had done, and it is exactly
    the assertion an operator would rely on when deciding to go live.

    So this reads evidence rather than intent: an authenticated record of a
    successful backup, an authenticated record of a successful restore drill,
    a valid key, a configured directory, and a recorded off-site destination.
    The records are MAC'd with a subkey of the backup key precisely so that
    "backups are healthy" cannot be asserted by anyone who can create a file.

    Each of the five is necessary. None of them, individually or together, can
    be satisfied by editing configuration, which is why the status is `BLOCKED`
    rather than `FAIL`.
    """
    from datetime import timedelta

    from app.services.database_backup import collect_evidence, evidence_shortfalls

    evidence = collect_evidence(settings)
    shortfalls = evidence_shortfalls(
        evidence,
        max_backup_age=timedelta(hours=int(settings.backup.max_backup_age_hours)),
        max_drill_age=timedelta(days=int(settings.backup.max_drill_age_days)),
    )
    if shortfalls:
        return Finding(
            "BACKUPS",
            Status.BLOCKED,
            f"{'; '.join(shortfalls)}. Tooling exists — see "
            "docs/operations/BACKUP_RUNBOOK.md — but a backup regime is proven by "
            "having run it, not by having written it.",
            classification=Classification.REQUIRED_AT_ACTIVATION,
        )
    return Finding(
        "BACKUPS",
        Status.PASS,
        "an authenticated record of a recent successful backup and a recent "
        "successful restore drill exists, with an off-site destination recorded.",
        classification=Classification.REQUIRED_AT_ACTIVATION,
    )


#: Every rule, in the order an operator should read them.
RULES: tuple[Rule, ...] = (
    Rule(
        "ENVIRONMENT",
        Classification.REQUIRED_NOW,
        False,
        "operator",
        "must be a deployed value",
        _check_environment,
        enforced_at_startup=False,
    ),
    Rule(
        "SECURITY_SECRET_KEY",
        Classification.REQUIRED_NOW,
        True,
        "operator secret store",
        "unique, >=32 chars, not the published placeholder",
        _check_secret_key,
        enforced_at_startup=True,
    ),
    Rule(
        "ALLOWED_HOSTS",
        Classification.REQUIRED_NOW,
        False,
        "operator",
        "explicit public hostnames, never a wildcard",
        _check_allowed_hosts,
        enforced_at_startup=True,
    ),
    Rule(
        "LOG_INCLUDE_REQUEST_BODY",
        Classification.REQUIRED_NOW,
        False,
        "operator",
        "false, so customer data stays out of logs",
        _check_request_body_logging,
        enforced_at_startup=True,
    ),
    Rule(
        "EBAY_MARKETPLACE_DELETION_ENDPOINT",
        Classification.REQUIRED_AT_ACTIVATION,
        False,
        "operator",
        "public https endpoint eBay can reach",
        _check_ebay_endpoint,
        enforced_at_startup=True,
    ),
    Rule(
        "SECURITY_ENCRYPTION_KEYS",
        Classification.REQUIRED_NOW,
        True,
        "operator secret store",
        "valid Fernet keys, none published, not the signing key",
        _check_encryption_keys,
        enforced_at_startup=True,
    ),
    Rule(
        "SECURITY_OTP_HMAC_KEY",
        Classification.REQUIRED_NOW,
        True,
        "operator secret store",
        "non-default and distinct from the signing key",
        _check_otp_key,
        enforced_at_startup=True,
    ),
    Rule(
        "SECURITY_COOKIE_SECURE",
        Classification.REQUIRED_NOW,
        False,
        "operator",
        "true",
        _check_cookie_secure,
        enforced_at_startup=True,
    ),
    Rule(
        "CORS_ORIGINS",
        Classification.REQUIRED_NOW,
        False,
        "operator",
        "the public https frontend origin",
        _check_cors,
        enforced_at_startup=False,
    ),
    Rule(
        "TRUSTED_PROXIES",
        Classification.REQUIRED_AT_ACTIVATION,
        False,
        "operator",
        "the CIDRs of the real edge, so client IPs are attributable",
        _check_trusted_proxies,
        enforced_at_startup=False,
    ),
    Rule(
        "NEXT_PUBLIC_API_URL",
        Classification.REQUIRED_AT_ACTIVATION,
        False,
        "frontend build",
        "public https API origin, inlined at build time",
        _check_frontend_api_url,
        enforced_at_startup=False,
    ),
    Rule(
        "GOOGLE_OAUTH_CLIENT_ID",
        Classification.REQUIRED_AT_ACTIVATION,
        False,
        "Google Cloud console",
        "set only when Google sign-in is activated",
        _check_google,
        enforced_at_startup=True,
    ),
    Rule(
        "POSTGRES_SSLMODE",
        Classification.REQUIRED_NOW,
        False,
        "operator",
        "verify-full with a CA bundle, against a managed database",
        _check_database_tls,
        enforced_at_startup=False,
    ),
    Rule(
        "TERMS_PUBLISHED",
        Classification.REQUIRED_AT_ACTIVATION,
        False,
        "legal",
        "true only after solicitor approval",
        _check_terms,
        enforced_at_startup=False,
    ),
    Rule(
        "EBAY_SELLER_CHANNEL",
        Classification.REQUIRED_AT_ACTIVATION,
        False,
        "operator",
        "off, or every eBay application setting present",
        _check_ebay_seller_channel,
        enforced_at_startup=False,
    ),
    Rule(
        "BACKUPS",
        Classification.REQUIRED_AT_ACTIVATION,
        False,
        "operator",
        "a recent verified backup and a recent successful restore drill, both recorded",
        _check_backups,
        enforced_at_startup=False,
    ),
)

#: Rules producing several findings, kept separate because a `Rule.check`
#: returns exactly one.
_MULTI_CHECKS: tuple[Callable[[Any], Iterable[Finding]], ...] = (_check_return_urls, _check_email)


@dataclass(frozen=True, slots=True)
class ReadinessReport:
    """Every finding, and what an operator should conclude."""

    findings: tuple[Finding, ...] = field(default_factory=tuple)

    def by_status(self, status: Status) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.status is status)

    @property
    def invalid(self) -> tuple[Finding, ...]:
        """Configuration that a deployed environment must not start with."""
        return tuple(f for f in self.findings if f.status is Status.FAIL)

    @property
    def blocked(self) -> tuple[Finding, ...]:
        return self.by_status(Status.BLOCKED)

    @property
    def missing(self) -> tuple[Finding, ...]:
        return self.by_status(Status.MISSING)

    @property
    def exit_code(self) -> int:
        """0 ready · 2 invalid · 3 publication blocked · 4 dependency missing.

        Ordered by severity, not by discovery: an invalid configuration is worse
        than a publication block, and both are worse than an absent activation
        dependency. `1` is reserved for the CLI's own usage and IO errors, so a
        broken invocation is never mistaken for a verdict about the environment.
        """
        if self.invalid:
            return 2
        if self.blocked:
            return 3
        if self.missing:
            return 4
        return 0


def evaluate(settings: Any) -> ReadinessReport:
    """Run every rule against a settings object. Starts nothing, prints nothing."""
    findings: list[Finding] = [rule.check(settings) for rule in RULES]
    for multi in _MULTI_CHECKS:
        findings.extend(multi(settings))
    return ReadinessReport(tuple(findings))


def startup_failure(settings: Any) -> str | None:
    """The message a deployed process should refuse to start with, or `None`.

    This is what `Settings.model_post_init` uses, so the boot guard and the CLI
    read the same table. Only rules marked `enforced_at_startup` are consulted,
    and only the first failure is returned — a process that must not start does
    not need a full report, and stopping at the first is the existing behaviour
    every current test asserts.
    """
    for rule in RULES:
        if not rule.enforced_at_startup:
            continue
        finding = rule.check(settings)
        if finding.status is Status.FAIL:
            return finding.reason
    return None


def rules_for(classification: Classification) -> Sequence[Rule]:
    return tuple(r for r in RULES if r.classification is classification)
