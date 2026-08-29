"""Transactional email (AUTH-G1).

Resend in deployment, a recording stub by default. Nothing here logs a
recipient, a provider response body, or the API key.
"""

from __future__ import annotations

from app.integrations.email.messages import send_password_reset_code
from app.integrations.email.provider import (
    EmailDeliveryError,
    EmailMessage,
    EmailProvider,
    ResendEmailProvider,
    SendBudget,
    StubEmailProvider,
    get_email_provider,
    reset_email_provider,
)

__all__ = [
    "EmailDeliveryError",
    "EmailMessage",
    "EmailProvider",
    "ResendEmailProvider",
    "SendBudget",
    "StubEmailProvider",
    "get_email_provider",
    "reset_email_provider",
    "send_password_reset_code",
]
