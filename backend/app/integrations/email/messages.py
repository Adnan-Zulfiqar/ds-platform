"""The messages this application sends. Currently one.

Kept apart from the provider so the wording is reviewable on its own and the
transport can be swapped without touching it.

**The code appears in the body and nowhere else.** Not in the subject, which
shows in notification previews on a lock screen; not in a log line; not in the
response to the browser that asked for it.
"""

from __future__ import annotations

from app.core.config import settings
from app.integrations.email.provider import EmailMessage, get_email_provider

__all__ = ["send_notification_email", "send_password_reset_code"]


async def send_notification_email(*, email: str, title: str, body: str, href: str | None) -> None:
    """Track E3: one notification to one recipient. Plain wording, the
    notification's own title and text, and a link back into the app."""
    from html import escape

    link = (
        f"{settings.email.app_base_url.rstrip('/')}{href}"
        if href and href.startswith("/")
        else None
    )
    text = f"{title}\n\n{body}\n" + (f"\nOpen in DropPilot: {link}\n" if link else "")
    text += "\nYou can choose which notifications you get by email in Settings → Notifications.\n"
    html = f"<p><strong>{escape(title)}</strong></p><p>{escape(body)}</p>"
    if link:
        html += f'<p><a href="{escape(link)}">Open in DropPilot</a></p>'
    html += (
        "<p>You can choose which notifications you get by email in Settings → Notifications.</p>"
    )
    await get_email_provider().send(
        EmailMessage(to=email, subject=f"DropPilot: {title}"[:200], text=text, html=html)
    )


def _body(code: str) -> tuple[str, str]:
    minutes = settings.password_reset.otp_ttl_seconds // 60
    text = (
        f"Your DropPilot AI password reset code is {code}.\n\n"
        f"It expires in {minutes} minutes and can be used once.\n\n"
        "If you did not ask to reset your password, you can ignore this message "
        "— your password has not changed. Nobody can use this code without also "
        "having access to this mailbox.\n\n"
        "We will never ask you for this code by phone, chat or email reply.\n"
    )
    html = (
        f"<p>Your DropPilot AI password reset code is <strong>{code}</strong>.</p>"
        f"<p>It expires in {minutes} minutes and can be used once.</p>"
        "<p>If you did not ask to reset your password, you can ignore this "
        "message — your password has not changed.</p>"
        "<p>We will never ask you for this code by phone, chat or email reply.</p>"
    )
    return text, html


async def send_password_reset_code(*, email: str, code: str) -> None:
    """Deliver a reset code. Raises `EmailDeliveryError` if it did not go."""
    text, html = _body(code)
    await get_email_provider().send(
        EmailMessage(
            to=email,
            # No code here: subjects show in lock-screen previews.
            subject="Your DropPilot AI password reset code",
            text=text,
            html=html,
        )
    )
