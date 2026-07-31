"""Application configuration.

All configuration enters the process here and nowhere else. Modules import the
``settings`` singleton rather than reading ``os.environ`` directly, which keeps
configuration validated in one place and makes tests able to override values.

Settings are grouped into cohesive nested models (database, redis, celery, ...)
so that a module can depend on the narrow slice it needs instead of a single
flat object with fifty attributes.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from typing import Annotated, ClassVar, Literal

from pydantic import (
    Field,
    PostgresDsn,
    RedisDsn,
    SecretStr,
    computed_field,
    field_validator,
)
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


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


class DatabaseSettings(BaseSettings):
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


class RedisSettings(BaseSettings):
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


class CelerySettings(BaseSettings):
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


class SecuritySettings(BaseSettings):
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

    rate_limit_enabled: bool = True
    rate_limit_requests: int = Field(default=100, ge=1, description="Requests per window.")
    rate_limit_window_seconds: int = Field(default=60, ge=1)

    hsts_max_age_seconds: int = Field(default=31_536_000, ge=0)

    # ClassVar, not a field. Without the annotation Pydantic would treat this as
    # a settable setting, which would let the very value used to detect an
    # insecure key be overridden from the environment.
    LOCAL_PLACEHOLDER_KEY: ClassVar[str] = "insecure-local-development-key-change-me"


class AliExpressSettings(BaseSettings):
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

    authorize_url: str = "https://api-sg.aliexpress.com/oauth/authorize"

    # rule fires on any name containing "token".
    token_url: str = "https://api-sg.aliexpress.com/rest/auth/token/create"  # noqa: S105
    refresh_url: str = "https://api-sg.aliexpress.com/rest/auth/token/refresh"
    api_base_url: str = "https://api-sg.aliexpress.com/sync"

    # Where AliExpress returns the user after consent. Must match the value
    # registered in the AliExpress developer console exactly — a mismatch is
    # rejected at the authorization step, before any code is issued.
    redirect_uri: str = "http://localhost:8000/api/v1/integrations/aliexpress/callback"

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


class ObservabilitySettings(BaseSettings):
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


class StorageSettings(BaseSettings):
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


class Settings(BaseSettings):
    """Root settings object."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

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
        default_factory=lambda: ["http://localhost:3000"]
    )
    allowed_hosts: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["*"])

    default_page_size: int = Field(default=25, ge=1)
    max_page_size: int = Field(
        default=100,
        ge=1,
        description="Hard ceiling; prevents a client from requesting a million rows.",
    )

    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    celery: CelerySettings = Field(default_factory=CelerySettings)
    security: SecuritySettings = Field(default_factory=SecuritySettings)
    observability: ObservabilitySettings = Field(default_factory=ObservabilitySettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)
    aliexpress: AliExpressSettings = Field(default_factory=AliExpressSettings)

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


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings singleton.

    Cached so that the ``.env`` file is parsed once. Tests override configuration
    with ``get_settings.cache_clear()`` after patching the environment.
    """
    return Settings()


settings = get_settings()
