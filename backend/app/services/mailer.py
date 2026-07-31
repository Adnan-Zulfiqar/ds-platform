"""Outbound mail abstraction.

Phase 7 ships a logging mailer only. It records that a message *would* be sent
and never pretends SMTP succeeded. A real provider (SES, Resend, …) plugs in
behind :class:`Mailer` without changing callers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class EmailMessage:
    to: str
    subject: str
    body_text: str


class Mailer(Protocol):
    async def send(self, message: EmailMessage) -> None: ...


class LoggingMailer:
    """Development/test mailer — logs intent, delivers nothing."""

    async def send(self, message: EmailMessage) -> None:
        logger.info(
            "mail_not_sent_no_provider",
            to_domain=message.to.split("@")[-1] if "@" in message.to else "unknown",
            subject=message.subject,
            body_length=len(message.body_text),
        )


def get_mailer() -> Mailer:
    return LoggingMailer()
