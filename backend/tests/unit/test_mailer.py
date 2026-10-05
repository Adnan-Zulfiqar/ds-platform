"""The logging mailer must never put an address or a body in the log."""

from __future__ import annotations

import pytest
from structlog.testing import capture_logs

from app.services.mailer import EmailMessage, LoggingMailer, get_mailer

pytestmark = pytest.mark.unit


async def test_the_logging_mailer_records_intent_without_the_address_or_body() -> None:
    message = EmailMessage(to="jane@example.com", subject="Your code", body_text="123456 secret")

    with capture_logs() as logs:
        await LoggingMailer().send(message)

    [entry] = [log for log in logs if log["event"] == "mail_not_sent_no_provider"]
    assert entry["to_domain"] == "example.com"
    assert entry["subject"] == "Your code"
    assert entry["body_length"] == len("123456 secret")
    flattened = str(entry)
    assert "jane" not in flattened
    assert "123456" not in flattened


async def test_an_address_without_a_domain_is_logged_as_unknown() -> None:
    with capture_logs() as logs:
        await LoggingMailer().send(EmailMessage(to="not-an-address", subject="s", body_text=""))
    [entry] = [log for log in logs if log["event"] == "mail_not_sent_no_provider"]
    assert entry["to_domain"] == "unknown"


def test_the_default_mailer_delivers_nothing() -> None:
    assert isinstance(get_mailer(), LoggingMailer)
