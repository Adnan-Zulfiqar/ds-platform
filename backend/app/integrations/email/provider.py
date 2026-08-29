"""Transactional email, behind a provider abstraction.

One interface, two implementations: Resend for deployment, and a stub that
records what it was asked to send and sends nothing. The stub is the **default**
so a test run, a developer machine, or a deployment that forgot to configure a
key mails nobody rather than silently mailing real people.

Two properties this module exists to guarantee:

* **The API key never leaves.** It is a `SecretStr`, it goes in one header, and
  nothing here logs a request, a response body, or a recipient address. A
  provider response quoting the recipient is exactly what a log aggregator
  should not accumulate.
* **The budget is ours, not the provider's.** Resend enforces its own quota, and
  finding out you have exhausted it is finding out too late. The breaker below
  is an application-side ceiling that fails closed before that point and says
  so.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Final, Protocol

import httpx

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

__all__ = [
    "EmailDeliveryError",
    "EmailMessage",
    "EmailProvider",
    "ResendEmailProvider",
    "SendBudget",
    "StubEmailProvider",
    "get_email_provider",
    "reset_email_provider",
]


class EmailDeliveryError(RuntimeError):
    """Delivery did not happen. Never carries a recipient or a provider body."""


@dataclass(frozen=True, slots=True)
class EmailMessage:
    """One message. Deliberately small — this is not a mail-merge engine."""

    to: str
    subject: str
    text: str
    html: str | None = None

    def __str__(self) -> str:  # pragma: no cover - diagnostic only
        # Reaches log lines; the recipient does not belong there.
        return f"<EmailMessage subject={self.subject!r}>"


class EmailProvider(Protocol):
    """What the application needs from an email service."""

    async def send(self, message: EmailMessage) -> str:
        """Deliver, returning a provider message id. Raises on failure."""
        ...


@dataclass
class SendBudget:
    """An application-side ceiling and circuit breaker.

    In-process and per-worker rather than shared in Redis, and that is a
    deliberate trade-off worth stating: a shared counter would be exact but puts
    the mail path behind a second network dependency that can itself fail. The
    purpose here is to stop a loop or a burst running away, and a per-worker
    ceiling does that. It is a backstop, not an accounting system.
    """

    max_per_hour: int
    failure_threshold: int
    cooldown_seconds: int
    _window_started: float = field(default_factory=time.monotonic)
    _sent_in_window: int = 0
    _consecutive_failures: int = 0
    _opened_at: float | None = None

    def _roll_window(self, now: float) -> None:
        if now - self._window_started >= 3600:
            self._window_started = now
            self._sent_in_window = 0

    def check(self) -> None:
        """Raise if a send must not be attempted."""
        now = time.monotonic()
        self._roll_window(now)

        if self._opened_at is not None:
            if now - self._opened_at < self.cooldown_seconds:
                raise EmailDeliveryError("email delivery is temporarily disabled")
            # Cooldown elapsed: allow one attempt through to test the water.
            self._opened_at = None
            self._consecutive_failures = 0

        if self._sent_in_window >= self.max_per_hour:
            logger.error("email_send_budget_exhausted", limit=self.max_per_hour)
            raise EmailDeliveryError("email send budget exhausted")

    def record_success(self) -> None:
        self._sent_in_window += 1
        self._consecutive_failures = 0

    def record_failure(self) -> None:
        self._sent_in_window += 1
        self._consecutive_failures += 1
        if self._consecutive_failures >= self.failure_threshold:
            self._opened_at = time.monotonic()
            logger.error("email_circuit_breaker_opened", failures=self._consecutive_failures)


class StubEmailProvider:
    """Records messages instead of sending them.

    Used by every automated test and by any environment that has not explicitly
    turned delivery on. `sent` is readable so a test can assert *that* a message
    was produced and what it said, without a network call.
    """

    def __init__(self) -> None:
        self.sent: list[EmailMessage] = []

    async def send(self, message: EmailMessage) -> str:
        self.sent.append(message)
        logger.info("email_stubbed", subject=message.subject)
        return f"stub-{len(self.sent)}"


class ResendEmailProvider:
    """Resend, over its documented HTTP API.

    `POST https://api.resend.com/emails` with a bearer key. The base URL is
    configuration rather than a literal so a test can point it at a mock
    transport — but a value naming any other host is refused outright, because
    an integration that can be redirected is an integration that can be made to
    post customer addresses somewhere else.
    """

    ALLOWED_HOSTS: Final[frozenset[str]] = frozenset({"api.resend.com"})

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        budget: SendBudget | None = None,
    ) -> None:
        configured_key = settings.resend.api_key
        self._api_key = (
            api_key
            if api_key is not None
            else (configured_key.get_secret_value() if configured_key else "")
        )
        if not self._api_key:
            raise EmailDeliveryError("RESEND_API_KEY is not configured")

        self._base_url = (base_url or settings.resend.api_base_url).rstrip("/")
        host = httpx.URL(self._base_url).host
        # A mock transport is how tests reach this class without a network, so
        # the host check is skipped only when one is supplied explicitly.
        if transport is None and host not in self.ALLOWED_HOSTS:
            raise EmailDeliveryError(f"refusing to send through unexpected host {host!r}")

        self._transport = transport
        self._budget = budget or SendBudget(
            max_per_hour=settings.email.max_sends_per_hour,
            failure_threshold=settings.email.breaker_failure_threshold,
            cooldown_seconds=settings.email.breaker_cooldown_seconds,
        )

    async def send(self, message: EmailMessage) -> str:
        self._budget.check()

        payload: dict[str, object] = {
            "from": settings.email.from_address,
            "to": [message.to],
            "subject": message.subject,
            "text": message.text,
        }
        if message.html:
            payload["html"] = message.html
        if settings.email.reply_to:
            payload["reply_to"] = settings.email.reply_to

        try:
            async with httpx.AsyncClient(
                timeout=settings.email.timeout_seconds, transport=self._transport
            ) as client:
                response = await client.post(
                    f"{self._base_url}/emails",
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                )
        except httpx.HTTPError as exc:
            self._budget.record_failure()
            # Type only: an httpx message can contain the URL, and a URL can
            # contain more than it should.
            logger.error("email_send_failed", provider="resend", reason=type(exc).__name__)
            raise EmailDeliveryError("email provider unavailable") from exc

        if response.status_code >= 400:
            self._budget.record_failure()
            # Status only. The body quotes the recipient address.
            logger.error("email_send_rejected", provider="resend", status=response.status_code)
            raise EmailDeliveryError("email provider rejected the message")

        # Fail closed on a response we do not understand rather than reporting a
        # send that may not have happened.
        try:
            message_id = str(response.json()["id"])
        except (ValueError, KeyError, TypeError) as exc:
            self._budget.record_failure()
            logger.error("email_send_unparseable_response", provider="resend")
            raise EmailDeliveryError("email provider returned an unexpected response") from exc

        self._budget.record_success()
        logger.info("email_sent", provider="resend", subject=message.subject)
        return message_id


_provider: EmailProvider | None = None


def get_email_provider() -> EmailProvider:
    """The configured provider, built once.

    Defaults to the stub. Delivery is opt-in.
    """
    global _provider
    if _provider is None:
        _provider = (
            ResendEmailProvider() if settings.email.sends_real_email else StubEmailProvider()
        )
    return _provider


def reset_email_provider() -> None:
    """Drop the cached provider. For tests and for configuration reloads."""
    global _provider
    _provider = None
