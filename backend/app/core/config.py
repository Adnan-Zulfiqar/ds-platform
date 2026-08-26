"""Application configuration.

All configuration enters the process here and nowhere else. Modules import the
``settings`` singleton rather than reading ``os.environ`` directly, which keeps
configuration validated in one place and makes tests able to override values.

Settings are grouped into cohesive nested models (database, redis, celery, ...)
so that a module can depend on the narrow slice it needs instead of a single
flat object with fifty attributes.
"""

from __future__ import annotations

import re
from enum import StrEnum
from functools import lru_cache
from ipaddress import IPv4Network, IPv6Network, ip_address, ip_network
from pathlib import Path
from typing import Annotated, ClassVar, Literal
from urllib.parse import urlsplit

from pydantic import (
    AliasChoices,
    Field,
    PostgresDsn,
    RedisDsn,
    SecretStr,
    computed_field,
    field_validator,
)
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# app/core/config.py → app/core → app → backend → repository root
_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_REPO_ROOT = _BACKEND_ROOT.parent

#: Env files every settings class reads, repository root first.
#:
#: Absolute, not a bare ".env". A relative name resolves against the *working
#: directory*, so launching from the repository root and from `backend/` would
#: read different files — or none — and the failure is silent: settings fall
#: back to defaults and the application starts looking healthy while pointed at
#: the wrong configuration.
_ENV_FILES = (_REPO_ROOT / ".env", _BACKEND_ROOT / ".env")


class _EnvFileSettings(BaseSettings):
    """Base for every settings group, carrying the env-file configuration.

    **This base is load-bearing, not decoration.** pydantic-settings reads
    ``env_file`` from the class being instantiated. A nested settings class that
    does not declare one reads ``os.environ`` and nothing else — so with the
    file configured only on the root ``Settings``, every nested group
    (``POSTGRES_*``, ``SECURITY_*``, ``ALIEXPRESS_*``, ...) silently ignored the
    ``.env`` and fell back to defaults.

    That failure mode is particularly nasty because it is invisible: the
    application boots, reports healthy, and runs on default credentials while
    the operator believes their ``.env`` was applied. It surfaced during Phase
    3.5 live setup, when app credentials present in the file were reported
    missing by the application.

    pydantic merges ``model_config`` across inheritance, so subclasses declare
    only their ``env_prefix`` and inherit the file configuration from here.
    """

    model_config = SettingsConfigDict(
        env_file=_ENV_FILES,
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )


def _is_internal_address(host: str) -> bool:
    """Whether ``host`` is an IP literal eBay could never reach.

    Only *literal* addresses are judged. A hostname that happens to resolve
    privately is not something configuration can settle — that is a DNS and
    network question — and pretending otherwise here would give false
    assurance.
    """
    try:
        address = ip_address(host)
    except ValueError:
        return False
    return bool(
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_unspecified
    )


#: eBay's documented verification-token character set: alphanumerics,
#: underscore and hyphen, and nothing else.
_EBAY_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]+")


class Environment(StrEnum):
    """Deployment environment.

    Behaviour that must differ between environments keys off this value rather
    than off an ad-hoc ``DEBUG`` flag, so the rules stay explicit and greppable.
    """

    LOCAL = "local"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"

    @property
    def is_deployed(self) -> bool:
        return self in (Environment.STAGING, Environment.PRODUCTION)


class DatabaseSettings(_EnvFileSettings):
    """PostgreSQL connection and pool configuration.

    Pool sizing matters at the scale this platform targets. Each API process
    holds its own pool, so the effective connection count against Postgres is
    ``(pool_size + max_overflow) * process_count``. Keep that product below the
    server's ``max_connections`` or use PgBouncer in front.
    """

    model_config = SettingsConfigDict(env_prefix="POSTGRES_", extra="ignore")

    host: str = "localhost"
    port: int = 5432
    user: str = "droppilot"
    password: SecretStr = SecretStr("droppilot")
    db: str = "droppilot"

    pool_size: int = Field(default=20, ge=1, description="Persistent connections per process.")
    max_overflow: int = Field(default=10, ge=0, description="Burst connections above pool_size.")
    pool_timeout: int = Field(default=30, ge=1, description="Seconds to wait for a connection.")
    pool_recycle: int = Field(
        default=1800,
        ge=-1,
        description="Recycle connections after N seconds; avoids stale conns behind proxies.",
    )
    pool_pre_ping: bool = Field(
        default=True,
        description=(
            "Validate a connection before use. Costs a round trip, "
            "prevents stale-connection errors."
        ),
    )
    echo_sql: bool = False

    @computed_field  # type: ignore[prop-decorator]
    @property
    def async_dsn(self) -> str:
        """DSN for the asyncpg driver, used by the application at runtime."""
        return str(
            PostgresDsn.build(
                scheme="postgresql+asyncpg",
                username=self.user,
                password=self.password.get_secret_value(),
                host=self.host,
                port=self.port,
                path=self.db,
            )
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def sync_dsn(self) -> str:
        """DSN for the psycopg driver.

        Alembic and Celery run synchronously, so they need a non-async driver
        even though the application itself is fully async.
        """
        return str(
            PostgresDsn.build(
                scheme="postgresql+psycopg",
                username=self.user,
                password=self.password.get_secret_value(),
                host=self.host,
                port=self.port,
                path=self.db,
            )
        )


class RedisSettings(_EnvFileSettings):
    """Redis configuration.

    Logical databases separate concerns so that flushing the cache can never
    destroy sessions or rate-limit counters.
    """

    model_config = SettingsConfigDict(env_prefix="REDIS_", extra="ignore")

    host: str = "localhost"
    port: int = 6379
    password: SecretStr | None = None

    cache_db: int = 0
    session_db: int = 1
    rate_limit_db: int = 2

    default_ttl_seconds: int = Field(default=300, ge=1)
    socket_timeout_seconds: int = Field(default=5, ge=1)
    max_connections: int = Field(default=50, ge=1)

    # The rate limiter runs on every request, so its Redis call is on the hot
    # path. It gets a much shorter timeout than the general client: when Redis
    # is unreachable the limiter fails open, and waiting five seconds to decide
    # that would add five seconds of latency to every request in the system.
    # A quarter of a second is far above a healthy local Redis round trip
    # (sub-millisecond) and far below anything a user would notice.
    fast_path_timeout_seconds: float = Field(default=0.25, gt=0)

    # Circuit breaker. After this many consecutive failures the rate limiter
    # stops calling Redis entirely for the cooldown period, so a sustained
    # outage costs one failed connection attempt per cooldown window rather
    # than one per request.
    circuit_breaker_threshold: int = Field(default=3, ge=1)
    circuit_breaker_cooldown_seconds: float = Field(default=10.0, gt=0)

    def dsn_for(self, db: int) -> str:
        password = self.password.get_secret_value() if self.password else None
        return str(
            RedisDsn.build(
                scheme="redis",
                password=password,
                host=self.host,
                port=self.port,
                path=str(db),
            )
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def cache_dsn(self) -> str:
        return self.dsn_for(self.cache_db)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def session_dsn(self) -> str:
        return self.dsn_for(self.session_db)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def rate_limit_dsn(self) -> str:
        return self.dsn_for(self.rate_limit_db)


class CelerySettings(_EnvFileSettings):
    """Celery and RabbitMQ configuration.

    RabbitMQ is the broker (durable routing, good operational tooling) while
    Redis holds results — results are short-lived and Redis reads are cheaper.
    """

    model_config = SettingsConfigDict(env_prefix="CELERY_", extra="ignore")

    broker_url: str = "amqp://droppilot:droppilot@localhost:5672//"
    result_backend: str = "redis://localhost:6379/3"

    task_default_queue: str = "default"
    task_soft_time_limit: int = Field(
        default=300, ge=1, description="Seconds before SoftTimeLimit is raised."
    )
    task_time_limit: int = Field(default=360, ge=1, description="Seconds before hard kill.")
    worker_prefetch_multiplier: int = Field(
        default=1,
        ge=1,
        description="1 keeps long tasks from being hoarded by one worker.",
    )
    worker_max_tasks_per_child: int = Field(
        default=1000,
        ge=1,
        description="Recycle workers to bound memory leaks in long-running processes.",
    )
    task_acks_late: bool = Field(
        default=True,
        description="Ack after completion so a crashed worker's task is redelivered.",
    )


class SecuritySettings(_EnvFileSettings):
    """Security parameters.

    Nothing here has a usable default for production — ``Settings`` refuses to
    start in a deployed environment while ``secret_key`` is still the local
    placeholder.
    """

    model_config = SettingsConfigDict(env_prefix="SECURITY_", extra="ignore")

    secret_key: SecretStr = SecretStr("insecure-local-development-key-change-me")

    @field_validator("secret_key")
    @classmethod
    def _reject_short_secret_key(cls, value: SecretStr) -> SecretStr:
        """Enforce a signing key long enough for the HMAC algorithms in use.

        RFC 7518 §3.2 requires an HS256 key of at least the hash output size —
        32 bytes. A shorter key reduces the effective security of every token
        the platform issues, and the failure is completely silent: short keys
        sign and verify perfectly well, they are just easier to brute-force.

        Enforced in every environment, not only deployed ones. A developer who
        sets a four-character key locally and then copies that habit into a
        secret manager is exactly the path this prevents.
        """
        minimum = 32
        if len(value.get_secret_value()) < minimum:
            raise ValueError(
                f"SECURITY_SECRET_KEY must be at least {minimum} characters "
                f"(RFC 7518 §3.2 for HS256). Generate one with: "
                f'python -c "import secrets; print(secrets.token_urlsafe(64))"'
            )
        return value

    # --- JWT ---------------------------------------------------------------
    #
    # HS256 (symmetric) is correct while one service both issues and verifies
    # tokens: there is no second party who needs to verify without also being
    # able to sign. Move to RS256 when a separate auth service, a third-party
    # verifier, or an edge gateway needs verification-only access — at that
    # point sharing the signing secret would let any of them mint tokens.
    jwt_algorithm: Literal["HS256", "HS384", "HS512"] = "HS256"
    jwt_issuer: str = "droppilot"
    jwt_audience: str = "droppilot-api"
    # Tolerance for clock skew between the issuing and verifying processes.
    jwt_leeway_seconds: int = Field(default=10, ge=0)

    # Short-lived by design. An access token cannot be revoked before it
    # expires — revocation happens on the refresh token — so its lifetime is
    # the window during which a stolen token remains usable.
    access_token_ttl_minutes: int = Field(default=15, ge=1)
    refresh_token_ttl_days: int = Field(default=30, ge=1)

    # --- Password policy ---------------------------------------------------
    #
    # Length dominates every other rule for real-world strength, so the minimum
    # is 12 rather than the common 8. Composition rules are configurable but
    # default to off apart from requiring more than one character class:
    # forcing symbols pushes users towards predictable substitutions
    # ("Password1!") without materially raising entropy.
    password_min_length: int = Field(default=12, ge=8)
    password_max_length: int = Field(
        default=128,
        ge=64,
        description="Upper bound. Argon2 has no truncation limit, but an "
        "unbounded password is a denial-of-service vector — hashing is "
        "deliberately expensive.",
    )
    password_require_uppercase: bool = True
    password_require_lowercase: bool = True
    password_require_digit: bool = True
    password_require_symbol: bool = False

    # --- Argon2id parameters ----------------------------------------------
    #
    # Defaults follow the OWASP recommendation (19 MiB, 2 iterations, 1 lane).
    # Raise memory_cost first if the hardware allows: memory is the dimension
    # an attacker finds hardest to parallelise.
    argon2_time_cost: int = Field(default=2, ge=1)
    argon2_memory_cost_kib: int = Field(default=19_456, ge=8192)
    argon2_parallelism: int = Field(default=1, ge=1)

    # --- Login throttling --------------------------------------------------
    #
    # Far stricter than the general API limit. Credential stuffing is a
    # high-volume attack and the endpoint is unauthenticated, so it is the most
    # exposed surface in the application.
    login_max_attempts: int = Field(default=5, ge=1)
    login_attempt_window_seconds: int = Field(default=300, ge=1)
    login_lockout_seconds: int = Field(default=900, ge=1)

    # --- Credential encryption --------------------------------------------
    #
    # Separate from `secret_key`, which signs tokens. Two reasons they must not
    # be the same value: a signing key can be rotated the moment a leak is
    # suspected at the cost of ending every session, whereas rotating the
    # encryption key requires re-encrypting stored data first. Sharing one key
    # would tie those two very different operations together.
    #
    # A list, ordered newest first. Decryption tries every key; encryption
    # always uses the first. That is what makes rotation possible without
    # downtime: prepend a new key, re-encrypt in the background, then drop the
    # old one.
    #
    # Each entry must be a urlsafe base64-encoded 32-byte Fernet key. Generate
    # with:
    #   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    encryption_keys: Annotated[list[SecretStr], NoDecode] = Field(
        default_factory=list,
        description="Fernet keys, newest first. Empty disables credential storage.",
    )

    @field_validator("encryption_keys", mode="before")
    @classmethod
    def _parse_encryption_keys(cls, value: object) -> object:
        """Accept a comma-separated list, matching every other list setting."""
        if not isinstance(value, str):
            return value
        return [item.strip() for item in value.split(",") if item.strip()]

    # --- Cookies -----------------------------------------------------------
    cookie_secure: bool = True
    cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    cookie_domain: str | None = None
    refresh_cookie_name: str = "droppilot_refresh"

    # Comma-separated CIDRs whose forwarding headers may be believed, e.g.
    #   SECURITY_TRUSTED_PROXIES=127.0.0.1/32,10.0.0.0/8
    #
    # **Empty by default, and that default is the safe one.** With no entry,
    # `X-Forwarded-For` and `CF-Connecting-IP` are ignored from every caller and
    # the socket peer is the identity. That is correct for a service reachable
    # directly, and it fails closed for one that has just gained a proxy: the
    # operator sees every client collapse to the proxy's address, which is a
    # visible misconfiguration, rather than the internet being able to forge
    # identities silently.
    #
    # `NoDecode` for the same reason as `cors_origins` — pydantic-settings would
    # otherwise `json.loads` the raw value before the validator runs, making the
    # documented comma-separated form unusable.
    trusted_proxies: Annotated[list[str], NoDecode] = Field(default_factory=list)

    @field_validator("trusted_proxies", mode="before")
    @classmethod
    def _split_trusted_proxies(cls, value: object) -> object:
        """Accept the comma-separated form documented in ``.env.example``."""
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @property
    def trusted_proxy_networks(self) -> tuple[IPv4Network | IPv6Network, ...]:
        """Configured CIDRs, parsed.

        Parsed on read rather than stored, so a malformed entry surfaces where
        the message can name it. Empty means nothing is trusted, which is the
        default and the safe answer — see ``app/core/client_ip.py``.
        """
        return _parse_trusted_proxies(self.trusted_proxies)

    rate_limit_enabled: bool = True
    rate_limit_requests: int = Field(default=100, ge=1, description="Requests per window.")
    rate_limit_window_seconds: int = Field(default=60, ge=1)

    # --- Email verification ------------------------------------------------
    #
    # Off by default: there is no mail provider yet, so flipping this without
    # SMTP would lock every new registration out. When a provider is wired,
    # set this true, set registration to create unverified users, and use
    # RequireVerified. See docs/PHASE_7_PLAN.md.
    require_email_verification: bool = False
    email_verification_ttl_hours: int = Field(default=24, ge=1, le=168)

    hsts_max_age_seconds: int = Field(default=31_536_000, ge=0)

    # ClassVar, not a field. Without the annotation Pydantic would treat this as
    # a settable setting, which would let the very value used to detect an
    # insecure key be overridden from the environment.
    LOCAL_PLACEHOLDER_KEY: ClassVar[str] = "insecure-local-development-key-change-me"

    #: Fernet keys published in this repository, and therefore public.
    #:
    #: They live in ``tests/conftest.py`` so that credential encryption and key
    #: rotation are exercised by the suite rather than assumed. Anything printed
    #: in a public repository is not a key — these decode to the literal ASCII
    #: ``test-key-N-NEVER-USE-IN-PROD-!!!`` precisely so that a reader can see
    #: that at a glance. A deployment that inherited one from a copied
    #: ``.env`` would encrypt every customer credential with a key any reader of
    #: this repository already has.
    PUBLISHED_TEST_ENCRYPTION_KEYS: ClassVar[frozenset[str]] = frozenset(
        {
            "dGVzdC1rZXktMS1ORVZFUi1VU0UtSU4tUFJPRC0hISE=",
            "dGVzdC1rZXktMi1ORVZFUi1VU0UtSU4tUFJPRC0hISE=",
        }
    )


class AliExpressSettings(_EnvFileSettings):
    """AliExpress Open Platform configuration.

    **App credentials are not here.** ``app_key`` and ``app_secret`` belong to
    the tenant, not the platform, so they live encrypted in
    ``aliexpress_connections``. Only values that are the same for every tenant
    are configured here.

    **The endpoints are configurable for a reason.** AliExpress operates several
    regional gateways and has changed paths between API generations. The
    defaults below reflect the documented Open Platform gateway, but they must
    be confirmed against current AliExpress developer documentation for the
    account in use before any live traffic — see docs/ALIEXPRESS_INTEGRATION.md.
    Making them settings means correcting one is a configuration change, not a
    code change and redeploy.
    """

    model_config = SettingsConfigDict(env_prefix="ALIEXPRESS_", extra="ignore")

    # --- Platform application credentials -----------------------------------
    #
    # **These belong to the platform operator, not to a tenant.** DropPilot
    # registers one AliExpress developer application; each seller authorises
    # *that* application through OAuth. A seller does not register their own.
    #
    # This is why they are configuration rather than per-tenant database rows:
    # requiring every customer to create an AliExpress developer account before
    # they could connect would make onboarding impossible.
    #
    # Per-tenant credential columns still exist on `aliexpress_connections`
    # from Phase 3 and take precedence when populated, so nothing already stored
    # breaks. See docs/ALIEXPRESS_INTEGRATION.md for the migration note.
    app_key: str = Field(
        default="",
        description="Platform AliExpress app key. Public; travels in every request URL.",
    )
    app_secret: SecretStr | None = Field(
        default=None,
        description="Platform AliExpress app secret. Never logged or returned.",
    )

    # Which AliExpress application status this deployment targets. An app in
    # `test` status can only be authorised by allow-listed accounts, so a
    # failure that looks like bad credentials is often just this.
    environment: Literal["test", "production"] = "test"

    authorize_url: str = "https://api-sg.aliexpress.com/oauth/authorize"

    # rule fires on any name containing "token".
    token_url: str = "https://api-sg.aliexpress.com/rest/auth/token/create"  # noqa: S105
    refresh_url: str = "https://api-sg.aliexpress.com/rest/auth/token/refresh"
    api_base_url: str = "https://api-sg.aliexpress.com/sync"

    # Where AliExpress returns the user after consent. Must match the value
    # registered in the AliExpress developer console exactly — a mismatch is
    # rejected at the authorization step, before any code is issued.
    #
    # Accepts either environment name. `ALIEXPRESS_CALLBACK_URL` matches the
    # AliExpress console's own wording; `ALIEXPRESS_REDIRECT_URI` is the OAuth
    # spec term and what Phase 3 shipped. Supporting both means renaming does
    # not silently fall back to the localhost default in an environment that is
    # still using the old name — which would break authorization while looking
    # like a credentials problem.
    callback_url: str = Field(
        default="http://localhost:8000/api/v1/integrations/aliexpress/callback",
        validation_alias=AliasChoices("ALIEXPRESS_CALLBACK_URL", "ALIEXPRESS_REDIRECT_URI"),
    )

    # Where the user lands in the application afterwards.
    frontend_return_url: str = "http://localhost:3000/settings/integrations"

    request_timeout_seconds: float = Field(default=15.0, gt=0)
    connect_timeout_seconds: float = Field(default=5.0, gt=0)

    max_retries: int = Field(
        default=3,
        ge=0,
        description="Retries for transient failures only; never for a 4xx.",
    )
    retry_backoff_seconds: float = Field(default=1.0, gt=0)
    retry_backoff_max_seconds: float = Field(default=30.0, gt=0)

    # Outbound quota. AliExpress enforces its own limits and answers with an
    # error code; staying under them locally avoids burning quota on requests
    # that will be rejected anyway.
    rate_limit_requests: int = Field(default=60, ge=1)
    rate_limit_window_seconds: int = Field(default=60, ge=1)

    # Refresh this far ahead of expiry rather than waiting for a 401, so a sync
    # is never interrupted by a token that lapsed mid-run.
    token_refresh_margin_seconds: int = Field(default=900, ge=0)

    # How long an OAuth `state` value stays valid. Long enough to read a consent
    # screen, short enough to bound replay.
    oauth_state_ttl_seconds: int = Field(default=600, ge=60)

    # --- Inbound webhooks --------------------------------------------------
    #
    # Optional HMAC secret. When set, deliveries must present a matching
    # signature header or they are rejected with 401. When unset (default),
    # the handler stays non-mutating and uses a shed-without-429 limiter
    # instead of the global rate limiter (see M11/M12).
    webhook_secret: SecretStr | None = Field(
        default=None,
        description="HMAC-SHA256 secret for inbound AliExpress webhooks.",
    )
    webhook_shed_limit: int = Field(
        default=120,
        ge=1,
        description="Max webhook deliveries processed per IP per window when unsigned.",
    )
    webhook_shed_window_seconds: int = Field(default=60, ge=1)


class EbayEnvironment(StrEnum):
    """Which eBay estate this deployment talks to.

    Chooses the Notification API host and nothing else in EBAY-C0. It is a real
    enum rather than a string so a typo cannot silently point production at the
    sandbox, which would make every signature verification fail against keys
    that do not exist.
    """

    SANDBOX = "sandbox"
    PRODUCTION = "production"


class EbaySettings(_EnvFileSettings):
    """eBay Developers Program application credentials and compliance endpoint.

    These are **platform** credentials belonging to DropPilot's own eBay
    application, not something a merchant types in. They are never surfaced to
    the frontend and never returned by an API — there is no response schema in
    this codebase capable of holding one.

    EBAY-C0 used only ``client_id``/``client_secret`` (for the client-credentials
    token that reads notification public keys) and the two marketplace-deletion
    fields. EBAY-C1 brings ``redirect_uri_name`` into use as the seller OAuth
    ``redirect_uri``. ``dev_id`` remains declared but unused — it belongs to the
    Traditional APIs, which this platform does not call.
    """

    model_config = SettingsConfigDict(env_prefix="EBAY_", extra="ignore")

    environment: EbayEnvironment = EbayEnvironment.PRODUCTION
    client_id: str = ""
    client_secret: SecretStr | None = None
    dev_id: str = ""

    #: The **RuName**, not a URL.
    #:
    #: eBay's authorization-code flow does not take a redirect URL in
    #: ``redirect_uri``; it takes an opaque "eBay Redirect URL name" that the
    #: portal issues, and the actual accept/decline URLs are configured against
    #: that RuName in the portal rather than sent in the request. Passing a URL
    #: here produces an opaque failure on eBay's own consent page, which is a
    #: disproportionately confusing thing to debug — hence the naming.
    redirect_uri_name: str = ""

    #: How long a seller has to complete consent before the state is discarded.
    #: Short by design: the state is a one-time CSRF credential, not a session.
    oauth_state_ttl_seconds: int = Field(default=600, ge=60, le=3600)

    #: Refresh this long before the access token actually expires.
    #:
    #: eBay's own guidance is to refresh reactively on an "Invalid access token"
    #: error rather than tracking lifetimes. This platform refreshes slightly
    #: early instead, because a merchant-facing action failing once so that the
    #: retry can succeed is a worse experience than one extra token call.
    token_refresh_margin_seconds: int = Field(default=300, ge=0, le=3600)

    #: Where the seller lands after consent, once the callback has finished.
    frontend_return_url: str = "http://localhost:3000/settings/integrations"

    request_timeout_seconds: float = Field(default=20.0, gt=0)
    connect_timeout_seconds: float = Field(default=5.0, gt=0)

    #: The exact, byte-for-byte URL registered in the eBay developer portal.
    #: It participates in the challenge hash, so a single character of
    #: difference — including a trailing slash — makes endpoint validation fail
    #: with no useful error from eBay. Never derived from a request header.
    marketplace_deletion_endpoint: str = ""
    marketplace_deletion_verification_token: SecretStr | None = None

    @property
    def notification_api_base(self) -> str:
        """Fixed eBay host for the Notification API.

        A constant per environment, never assembled from anything a caller
        supplies. That is what makes SSRF impossible on the public-key path:
        the only attacker-influenced value is a key id, and it is validated as
        a UUID before it is used.
        """
        if self.environment is EbayEnvironment.SANDBOX:
            return "https://api.sandbox.ebay.com"
        return "https://api.ebay.com"

    @property
    def oauth_authorize_url(self) -> str:
        """eBay's consent page. A different host from the API — ``auth.``, not ``api.``."""
        if self.environment is EbayEnvironment.SANDBOX:
            return "https://auth.sandbox.ebay.com/oauth2/authorize"
        return "https://auth.ebay.com/oauth2/authorize"

    @property
    def oauth_token_url(self) -> str:
        """The token service, shared by all three grant types."""
        return f"{self.notification_api_base}/identity/v1/oauth2/token"

    @property
    def identity_api_base(self) -> str:
        """The Identity API lives on ``apiz.``, not ``api.``.

        A genuine eBay quirk rather than a typo: ``getUser`` is served from a
        different host from every other endpoint this platform calls, and
        pointing it at ``api.`` returns a 404 that reads like a permissions
        problem.
        """
        if self.environment is EbayEnvironment.SANDBOX:
            return "https://apiz.sandbox.ebay.com"
        return "https://apiz.ebay.com"

    @property
    def is_oauth_configured(self) -> bool:
        """Whether a seller connection can be started at all.

        Fails closed the same way ``is_deletion_configured`` does: without a
        RuName the consent request would be built with an empty ``redirect_uri``
        and rejected by eBay with nothing useful in the response.
        """
        secret = self.client_secret
        return bool(
            self.client_id.strip()
            and secret is not None
            and secret.get_secret_value().strip()
            and self.redirect_uri_name.strip()
        )

    @property
    def is_deletion_configured(self) -> bool:
        """Whether the compliance endpoint can answer at all.

        Fails closed: an unconfigured deployment refuses the challenge rather
        than hashing an empty token, which would produce a stable-looking but
        meaningless digest that eBay would reject anyway — after telling the
        operator nothing about why.
        """
        token = self.marketplace_deletion_verification_token
        return bool(
            self.marketplace_deletion_endpoint.strip()
            and token is not None
            and token.get_secret_value().strip()
        )

    @field_validator("marketplace_deletion_verification_token")
    @classmethod
    def _validate_verification_token(cls, value: SecretStr | None) -> SecretStr | None:
        """eBay's documented token rule, enforced at configuration load.

        *"The verification token has to be between 32 and 80 characters, and
        allowed characters include alphanumeric characters, underscore (_), and
        hyphen (-). No other characters are allowed."* Checking it here means a
        bad token is a boot failure with a clear message, not a silent endpoint
        validation failure in the eBay portal days later.

        The token itself never appears in the error — only its length and
        whether the character set was the problem.
        """
        if value is None:
            return None
        raw = value.get_secret_value()
        if not raw:
            return value
        if not 32 <= len(raw) <= 80:
            raise ValueError(
                "EBAY_MARKETPLACE_DELETION_VERIFICATION_TOKEN must be 32-80 characters "
                f"(got {len(raw)})."
            )
        if not _EBAY_TOKEN_PATTERN.fullmatch(raw):
            raise ValueError(
                "EBAY_MARKETPLACE_DELETION_VERIFICATION_TOKEN may contain only letters, "
                "digits, underscore and hyphen."
            )
        return value

    @field_validator("marketplace_deletion_endpoint")
    @classmethod
    def _validate_endpoint_shape(cls, value: str) -> str:
        """Reject shapes eBay itself refuses, without normalising anything.

        Deliberately **not** normalising: adding or removing a trailing slash
        here would silently change the challenge hash and break the very
        validation this setting exists for. What is registered in the portal is
        what must be configured, character for character.

        Deployment-sensitive rules (HTTPS, no localhost or internal address)
        are applied in ``_validate_ebay_deletion_endpoint`` on the composed
        settings, where the environment is known.
        """
        candidate = value.strip()
        if not candidate:
            return candidate
        if candidate != value:
            raise ValueError(
                "EBAY_MARKETPLACE_DELETION_ENDPOINT must not have leading or trailing "
                "whitespace; it is hashed byte-for-byte."
            )
        parts = urlsplit(candidate)
        if parts.scheme not in {"http", "https"}:
            raise ValueError("EBAY_MARKETPLACE_DELETION_ENDPOINT must be an http(s) URL.")
        if not parts.hostname:
            raise ValueError("EBAY_MARKETPLACE_DELETION_ENDPOINT must name a host.")
        if parts.username or parts.password:
            raise ValueError("EBAY_MARKETPLACE_DELETION_ENDPOINT must not carry userinfo.")
        if parts.fragment or parts.query:
            raise ValueError(
                "EBAY_MARKETPLACE_DELETION_ENDPOINT must not carry a query or fragment."
            )
        return candidate


def _parse_trusted_proxies(values: list[str]) -> tuple[IPv4Network | IPv6Network, ...]:
    """Parse configured CIDRs, refusing anything that is not one.

    A malformed entry is a boot failure rather than a silently dropped rule: a
    trusted-proxy list that quietly lost a network would make every client
    behind it share one identity, and nothing would say so.

    A bare address is accepted and read as a single host (``/32`` or ``/128``),
    because that is what an operator writing ``127.0.0.1`` means.
    """
    networks: list[IPv4Network | IPv6Network] = []
    for raw in values:
        candidate = raw.strip()
        if not candidate:
            continue
        try:
            networks.append(ip_network(candidate, strict=False))
        except ValueError as exc:
            raise ValueError(
                f"SECURITY_TRUSTED_PROXIES contains {candidate!r}, which is not an "
                "IPv4/IPv6 address or CIDR."
            ) from exc
    return tuple(networks)


class ShopifySettings(_EnvFileSettings):
    """Shopify Partner / custom app configuration.

    ``api_key`` and ``api_secret`` belong to the DropPilot Shopify app. Per-shop
    access tokens live encrypted in ``shopify_connections``.
    """

    model_config = SettingsConfigDict(env_prefix="SHOPIFY_", extra="ignore")

    api_key: str = Field(default="", description="Shopify app API key (client id).")
    api_secret: SecretStr | None = Field(
        default=None,
        description="Shopify app API secret. Never logged or returned.",
    )
    scopes: str = Field(
        default=(
            "read_products,write_products,read_inventory,write_inventory,read_orders,read_locations"
        ),
        description="OAuth scopes requested at install time.",
    )
    api_version: str = Field(
        default="2026-07",
        description=(
            "Shopify Admin **REST** API version. Legacy REST callers read this "
            "setting and only this one; GraphQL reads `graphql_api_version`."
        ),
    )
    graphql_api_version: str = Field(
        default="2026-07",
        description=(
            "Shopify Admin GraphQL API version. Deliberately separate from the "
            "REST setting: the two surfaces are on different migration clocks, "
            "and one shared value would let a REST pin silently drag GraphQL "
            "backwards (or the reverse) with no reviewer noticing."
        ),
    )

    @field_validator("graphql_api_version", "api_version")
    @classmethod
    def _reject_unpinned_api_version(cls, value: str) -> str:
        """Require an explicit ``YYYY-MM`` quarter — never ``latest``.

        Shopify accepts ``/admin/api/latest/``, and it is a trap: the schema
        changes under a deployed app four times a year with no code change and
        no deploy to correlate against. Every request this platform makes names
        the version it was written for, so an upgrade is a reviewed edit.
        """
        candidate = value.strip()
        if not re.fullmatch(r"\d{4}-(01|04|07|10)", candidate):
            raise ValueError(
                "Shopify API version must be a pinned quarterly release such as "
                "'2026-07'. 'latest', 'unstable' and unpinned values are refused."
            )
        return candidate

    callback_url: str = Field(
        default="http://localhost:8000/api/v1/integrations/shopify/callback",
    )
    frontend_return_url: str = "http://localhost:3000/settings/integrations"
    webhook_callback_base: str = Field(
        default="http://localhost:8000/api/v1/integrations/shopify/webhooks",
        description="Public base URL Shopify POSTs webhooks to.",
    )
    oauth_state_ttl_seconds: int = Field(default=600, ge=60)
    request_timeout_seconds: float = Field(default=20.0, gt=0)
    connect_timeout_seconds: float = Field(default=5.0, gt=0)
    max_retries: int = Field(default=3, ge=0)
    rate_limit_requests: int = Field(default=40, ge=1)
    rate_limit_window_seconds: int = Field(default=1, ge=1)


class AIProviderName(StrEnum):
    """Which backend answers `AIProvider` calls — see `app.ai.provider`.

    Listed here in full, including providers with no implementation yet, so
    the operator-facing configuration surface does not change shape as each
    one ships. Selecting one that is not yet implemented is a configuration
    error (`app.ai.factory` raises `AIProviderNotConfiguredError`), not a
    silent fallback to `STUB` — a deployment that asked for a real provider
    must find out immediately, not by noticing the copy looks fake.
    """

    STUB = "stub"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GEMINI = "gemini"
    LOCAL = "local"


class AISettings(_EnvFileSettings):
    """AI provider selection and credentials.

    Keys live here rather than per-tenant. Unlike AliExpress or Shopify —
    where each tenant authorises their own supplier or store — DropPilot
    calls the model on the platform's own account; a tenant never supplies
    their own OpenAI key.
    """

    model_config = SettingsConfigDict(env_prefix="AI_", extra="ignore")

    provider: AIProviderName = AIProviderName.STUB

    openai_api_key: SecretStr | None = Field(
        default=None, description="Platform OpenAI API key. Never logged or returned."
    )
    anthropic_api_key: SecretStr | None = Field(
        default=None, description="Platform Anthropic API key. Never logged or returned."
    )
    # `GOOGLE_API_KEY` matches Google's own client libraries; `GEMINI_API_KEY`
    # is the name most third-party examples use. Supporting both means
    # whichever one an operator already has set from following either
    # convention works, rather than silently falling back to STUB because the
    # name did not match.
    google_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("AI_GOOGLE_API_KEY", "AI_GEMINI_API_KEY"),
        description="Platform Google/Gemini API key. Never logged or returned.",
    )
    local_base_url: str | None = Field(
        default=None,
        description="Base URL for a self-hosted model server, used when provider=local.",
    )

    request_timeout_seconds: float = Field(default=30.0, gt=0)
    connect_timeout_seconds: float = Field(default=5.0, gt=0)
    max_retries: int = Field(
        default=2, ge=0, description="Retries for transient failures only; never for a 4xx."
    )
    allow_prompt_mutation: bool = Field(
        default=False,
        description=(
            "When false (default), create/version/activate of platform-global "
            "AI prompts is refused. Tenant admins must not edit prompts that "
            "affect every workspace (audit A-03). Enable only for platform "
            "operators or automated tests."
        ),
    )


class ObservabilitySettings(_EnvFileSettings):
    """Logging and monitoring configuration."""

    model_config = SettingsConfigDict(env_prefix="LOG_", extra="ignore")

    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    json_output: bool = Field(
        default=True,
        description="JSON in deployed environments; human-readable console locally.",
    )
    include_request_body: bool = Field(
        default=False,
        description="Never enable in production — request bodies carry customer data.",
    )
    slow_request_ms: int = Field(default=1000, ge=1)


class StorageSettings(_EnvFileSettings):
    """Object storage (AWS S3) configuration."""

    model_config = SettingsConfigDict(env_prefix="S3_", extra="ignore")

    bucket: str = "droppilot-local"
    region: str = "eu-west-1"
    access_key_id: SecretStr | None = None
    secret_access_key: SecretStr | None = None
    endpoint_url: str | None = Field(
        default=None,
        description="Override for S3-compatible local storage (MinIO); None uses real AWS.",
    )
    presigned_url_ttl_seconds: int = Field(default=3600, ge=1)


class FxSettings(_EnvFileSettings):
    """Exchange-rate provider configuration.

    Default provider is ``unavailable`` so cross-currency pricing blocks rather
    than inventing a 1:1 rate. Use ``stub`` only in automated tests.
    """

    model_config = SettingsConfigDict(env_prefix="FX_", extra="ignore")

    provider: str = Field(
        default="unavailable",
        description="unavailable | stub | openexchangerates",
    )
    api_key: SecretStr | None = Field(
        default=None,
        description="Provider API key when required. Never commit real keys.",
    )
    base_url: str = Field(
        default="https://openexchangerates.org/api",
        description="Open Exchange Rates API root (no trailing slash required).",
    )
    timeout_seconds: int = Field(default=10, ge=1, le=120)
    #: Freshness window — quotes younger than this (by provider_timestamp) are CURRENT.
    cache_ttl_seconds: int = Field(default=3600, ge=1)
    #: Redis retention and outer bound for controlled stale fallback.
    max_staleness_seconds: int = Field(default=21600, ge=1)
    #: Legacy freshness knob (minutes). Prefer cache_ttl_seconds.
    rate_max_age_minutes: int = Field(
        default=60,
        ge=1,
        description="Deprecated: prefer FX_CACHE_TTL_SECONDS for freshness.",
    )


class Settings(_EnvFileSettings):
    """Root settings object.

    Inherits its env-file configuration from `_EnvFileSettings` like every other
    group, so there is exactly one definition of where configuration is read
    from. It previously declared its own, which is how the nested groups came to
    be missing theirs without anyone noticing.
    """

    environment: Environment = Environment.LOCAL
    project_name: str = "DropPilot AI"
    api_v1_prefix: str = "/api/v1"

    # Comma-separated in the environment, e.g.
    #   CORS_ORIGINS=http://localhost:3000,https://app.droppilot.ai
    #
    # `NoDecode` is essential, not decoration. pydantic-settings treats any
    # list-typed field as "complex" and runs `json.loads` on the raw environment
    # value *before* field validators execute — so a plain comma-separated
    # string raised `JSONDecodeError` at startup and the validator below never
    # ran. That made the format documented in `.env.example` unusable, and the
    # failure was a stack trace during boot rather than anything actionable.
    #
    # `NoDecode` suppresses that pre-parse and hands the raw string to the
    # validator, which accepts both the comma-separated and JSON-array forms.
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000", "http://localhost"]
    )
    allowed_hosts: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["*"])

    default_page_size: int = Field(default=25, ge=1)
    max_page_size: int = Field(
        default=100,
        ge=1,
        description="Hard ceiling; prevents a client from requesting a million rows.",
    )

    #: Fallback AliExpress ship-to when the dialog/store/history cannot supply
    #: one. Leave blank so merchants must choose rather than silently defaulting
    #: every workspace to US (live: some listings return rsp_code 482 for US).
    default_ship_to_country: str = Field(
        default="",
        description="Optional ISO 3166-1 alpha-2 default for AliExpress imports.",
    )

    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    celery: CelerySettings = Field(default_factory=CelerySettings)
    security: SecuritySettings = Field(default_factory=SecuritySettings)
    observability: ObservabilitySettings = Field(default_factory=ObservabilitySettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)
    fx: FxSettings = Field(default_factory=FxSettings)
    aliexpress: AliExpressSettings = Field(default_factory=AliExpressSettings)
    shopify: ShopifySettings = Field(default_factory=ShopifySettings)
    ebay: EbaySettings = Field(default_factory=EbaySettings)
    ai: AISettings = Field(default_factory=AISettings)

    @field_validator("cors_origins", "allowed_hosts", mode="before")
    @classmethod
    def _parse_string_list(cls, value: object) -> object:
        """Accept both a JSON array and a plain comma-separated string.

        Docker Compose and most secret managers only deal in flat strings, so
        requiring JSON would be constant deployment friction — but some secret
        managers *do* emit JSON, so both must work.

        Because these fields are annotated ``NoDecode``, pydantic-settings hands
        over the raw string and performs no parsing of its own. This validator is
        therefore the only place either format is understood, and it must handle
        both.
        """
        if not isinstance(value, str):
            return value

        stripped = value.strip()
        if not stripped:
            return []

        if stripped.startswith("["):
            import json

            try:
                return json.loads(stripped)
            except json.JSONDecodeError as exc:
                # A value that looks like JSON but is malformed is a typo, not a
                # comma-separated list. Saying so beats the confusing
                # "input should be a valid list" that would follow.
                raise ValueError(
                    f"Value looks like a JSON array but could not be parsed: {exc}"
                ) from exc

        return [item.strip() for item in stripped.split(",") if item.strip()]

    @property
    def docs_enabled(self) -> bool:
        """Interactive API docs are a reconnaissance aid; keep them off in production."""
        return self.environment is not Environment.PRODUCTION

    def model_post_init(self, __context: object) -> None:
        """Fail fast on insecure configuration in deployed environments.

        Refusing to boot is deliberately harsher than logging a warning: a
        warning in a container log is a warning nobody reads.
        """
        if not self.environment.is_deployed:
            return

        placeholder = SecuritySettings.LOCAL_PLACEHOLDER_KEY
        if self.security.secret_key.get_secret_value() == placeholder:
            raise ValueError(
                f"SECURITY_SECRET_KEY is still the local placeholder in "
                f"{self.environment}. Set a unique secret before deploying."
            )
        if "*" in self.allowed_hosts:
            raise ValueError(
                f"ALLOWED_HOSTS must not be a wildcard in {self.environment}; "
                "list the real hostnames to prevent Host header attacks."
            )
        if self.observability.include_request_body:
            raise ValueError(
                "LOG_INCLUDE_REQUEST_BODY must be false in deployed environments; "
                "request bodies contain customer data."
            )

        self._verify_ebay_deletion_endpoint()

        self._validate_deployed_encryption()

        if not self.security.cookie_secure:
            # The refresh cookie is the longest-lived credential the browser
            # holds. Without Secure it is sent over plain HTTP, where anyone on
            # the path can lift it and mint access tokens for thirty days.
            #
            # It is weakened locally on purpose — the test client speaks HTTP,
            # and a Secure cookie would simply never be sent — which is exactly
            # why the deployed case needs a guard rather than a convention.
            raise ValueError(
                f"SECURITY_COOKIE_SECURE must be true in {self.environment}; "
                "the refresh cookie would otherwise travel over plain HTTP."
            )

    def _verify_ebay_deletion_endpoint(self) -> None:
        """Deployed-environment rules for the eBay compliance endpoint.

        eBay states the endpoint *"should use the 'https' protocol, and it
        should not contain an internal IP address or 'localhost' in its path"*.
        Enforced at boot because the failure it prevents is otherwise silent:
        eBay simply refuses to validate the endpoint and the keyset stays
        inactive, with nothing in this application's logs to say why.

        Local and test environments keep http/localhost so the receiver can be
        exercised without a tunnel.
        """
        endpoint = self.ebay.marketplace_deletion_endpoint
        if not endpoint:
            return
        parts = urlsplit(endpoint)
        if parts.scheme != "https":
            raise ValueError(
                "EBAY_MARKETPLACE_DELETION_ENDPOINT must use https in "
                f"{self.environment}; eBay rejects plaintext endpoints."
            )
        host = (parts.hostname or "").lower()
        if host in {"localhost", "127.0.0.1", "::1"} or host.endswith(".localhost"):
            raise ValueError(
                "EBAY_MARKETPLACE_DELETION_ENDPOINT must not be localhost in "
                f"{self.environment}; eBay must be able to reach it."
            )
        if _is_internal_address(host):
            raise ValueError(
                "EBAY_MARKETPLACE_DELETION_ENDPOINT must not be an internal IP address in "
                f"{self.environment}; eBay must be able to reach it."
            )

    def _validate_deployed_encryption(self) -> None:
        """Refuse to deploy with unusable or publicly-known encryption keys.

        Separate from the signing-key checks because the failure mode is
        different and worse. A bad signing key breaks authentication loudly and
        immediately. A bad *encryption* key does not: the application starts,
        reports healthy, serves traffic, and only fails when someone connects a
        supplier — by which time the deployment looks fine and the error appears
        to be an integration problem.
        """
        keys = [key.get_secret_value() for key in self.security.encryption_keys]

        if not keys:
            raise ValueError(
                f"SECURITY_ENCRYPTION_KEYS is empty in {self.environment}. "
                "Credential storage would fail at first use rather than at "
                'startup. Generate one with: python -c "from '
                "cryptography.fernet import Fernet; "
                'print(Fernet.generate_key().decode())"'
            )

        published = SecuritySettings.PUBLISHED_TEST_ENCRYPTION_KEYS.intersection(keys)
        if published:
            raise ValueError(
                f"SECURITY_ENCRYPTION_KEYS contains a key published in this "
                f"repository's test suite, in {self.environment}. It is public; "
                "generate a new one and re-encrypt any stored credentials."
            )

        if self.security.secret_key.get_secret_value() in keys:
            # Two keys with different rotation stories. A signing key can be
            # replaced the moment a leak is suspected, at the cost of ending
            # every session. An encryption key cannot — stored ciphertext has
            # to be re-encrypted first. Sharing one value means an urgent
            # rotation is blocked on a slow migration.
            raise ValueError(
                "SECURITY_SECRET_KEY must not also appear in "
                "SECURITY_ENCRYPTION_KEYS. They are rotated on different "
                "schedules and sharing one value makes urgent rotation "
                "impossible without re-encrypting stored data first."
            )


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings singleton.

    Cached so that the ``.env`` file is parsed once. Tests override configuration
    with ``get_settings.cache_clear()`` after patching the environment.
    """
    return Settings()


settings = get_settings()
