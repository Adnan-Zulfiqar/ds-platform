"""Track E3: notifications by email, from an outbox.

``NotificationService.notify`` writes every notification except INFO with
``email_status = 'pending'``. A beat task walks each active workspace and
calls :meth:`NotificationEmailService.deliver_pending`, which picks the
recipients, applies their preferences and sends through the configured email
provider (the recording stub unless deployment enables Resend).

Why an outbox rather than sending inside ``notify``: notifications are raised
from requests and from Celery tasks alike, often inside a transaction that
may still roll back. A row that commits is a notification that happened;
mailing before the commit would announce things that then did not happen.

Delivery is at-least-once: a crash after the provider accepted a message but
before the status commit can send it again on the next sweep. Rows are taken
with ``SKIP LOCKED`` so two sweeps never race on one notification.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ValidationError
from app.core.logging import get_logger
from app.integrations.email.messages import send_notification_email
from app.integrations.email.provider import EmailDeliveryError
from app.models.notification import Notification, NotificationKind
from app.models.user import User
from app.repositories.notification import (
    NotificationEmailPreferenceRepository,
    NotificationRepository,
)
from app.repositories.user import UserRepository

logger = get_logger(__name__)

#: What a user gets by email until they choose otherwise: the things that need
#: action. Routine progress (imports finished, prices moved) stays in-app.
DEFAULT_EMAIL_KINDS: Final[frozenset[str]] = frozenset(
    {
        NotificationKind.SYNC_FAILED.value,
        NotificationKind.WEBHOOK_FAILURE.value,
        NotificationKind.TASK_FAILURE.value,
        NotificationKind.AUTOMATION_FAILED.value,
    }
)
#: Kinds a user may opt into. INFO never leaves the app.
EMAILABLE_KINDS: Final[tuple[str, ...]] = tuple(
    kind.value for kind in NotificationKind if kind is not NotificationKind.INFO
)
#: Workspace-wide notifications go to the people who can act on them.
_WORKSPACE_ROLES: Final = ("owner", "admin")


@dataclass(frozen=True, slots=True)
class DeliveryOutcome:
    sent: int
    skipped: int
    failed: int


class NotificationEmailService:
    def __init__(self, session: AsyncSession) -> None:
        self.notifications = NotificationRepository(session)
        self.preferences = NotificationEmailPreferenceRepository(session)
        self.users = UserRepository(session)

    # --- preferences -------------------------------------------------------

    async def preferences_for(self, user_id: uuid.UUID) -> list[str]:
        row = await self.preferences.get_for_user(user_id)
        return sorted(row.kinds) if row is not None else sorted(DEFAULT_EMAIL_KINDS)

    async def set_preferences(self, user_id: uuid.UUID, kinds: list[str]) -> list[str]:
        unknown = sorted(set(kinds) - set(EMAILABLE_KINDS))
        if unknown:
            raise ValidationError("Unknown notification kinds.", details={"unknown": unknown})
        clean = sorted(set(kinds))
        row = await self.preferences.get_for_user(user_id)
        if row is None:
            await self.preferences.create(user_id=user_id, kinds=clean)
        else:
            await self.preferences.update(row, kinds=clean)
        return clean

    # --- delivery ----------------------------------------------------------

    async def deliver_pending(self, *, limit: int = 50) -> DeliveryOutcome:
        pending = await self.notifications.pending_email(limit=limit)
        if not pending:
            return DeliveryOutcome(0, 0, 0)
        admins = await self.users.active_with_roles(_WORKSPACE_ROLES)
        sent = skipped = failed = 0
        for notification in pending:
            recipients = await self._recipients(notification, admins)
            if not recipients:
                notification.email_status = "skipped"
                skipped += 1
                continue
            try:
                for email in recipients:
                    await send_notification_email(
                        email=email,
                        title=notification.title,
                        body=notification.body,
                        href=notification.href,
                    )
            except EmailDeliveryError:
                # Recorded, not retried forever: a broken provider must not
                # turn into an ever-growing backlog mailed in a burst later.
                notification.email_status = "failed"
                failed += 1
                logger.warning("notification_email_failed", notification_id=str(notification.id))
                continue
            notification.email_status = "sent"
            notification.emailed_at = datetime.now(UTC)
            sent += 1
        logger.info("notification_emails_delivered", sent=sent, skipped=skipped, failed=failed)
        return DeliveryOutcome(sent, skipped, failed)

    async def _recipients(self, notification: Notification, admins: list[User]) -> list[str]:
        if notification.user_id is not None:
            user = await self.users.get_by_id(notification.user_id)
            candidates = [user] if user is not None and user.is_active else []
        else:
            candidates = list(admins)
        if not candidates:
            return []
        prefs = await self.preferences.for_users([u.id for u in candidates])
        kind = notification.kind.value
        return [u.email for u in candidates if kind in prefs.get(u.id, DEFAULT_EMAIL_KINDS)]


__all__ = [
    "DEFAULT_EMAIL_KINDS",
    "EMAILABLE_KINDS",
    "DeliveryOutcome",
    "NotificationEmailService",
]
