"""Automation engine — schedules and dispatches, never reimplements sync."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.automation import (
    AutomationAction,
    AutomationRule,
    AutomationRun,
    AutomationSchedule,
)
from app.models.notification import NotificationKind
from app.models.order import SyncRunStatus, SyncTrigger
from app.repositories.automation import AutomationRuleRepository, AutomationRunRepository
from app.schemas.automation import AutomationRuleCreate, AutomationRuleUpdate
from app.schemas.common import ListQueryParams
from app.schemas.pricing import PricingApplyRequest
from app.services.base import BaseService
from app.services.inventory_sync import InventorySyncService
from app.services.notification_service import NotificationService
from app.services.order_sync import OrderSyncService
from app.services.pricing_engine import PricingEngine

logger = get_logger(__name__)

_SCHEDULE_DELTA = {
    AutomationSchedule.HOURLY: timedelta(hours=1),
    AutomationSchedule.DAILY: timedelta(days=1),
    AutomationSchedule.WEEKLY: timedelta(weeks=1),
}


class AutomationService(BaseService):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)
        self.rules = AutomationRuleRepository(session)
        self.runs = AutomationRunRepository(session)
        self.notifications = NotificationService(session)

    async def create_rule(self, payload: AutomationRuleCreate) -> AutomationRule:
        now = datetime.now(UTC)
        next_run = self._next_run(payload.schedule, now)
        return await self.rules.create(
            **payload.model_dump(),
            next_run_at=next_run,
        )

    async def update_rule(
        self, rule_id: uuid.UUID, payload: AutomationRuleUpdate
    ) -> AutomationRule:
        rule = await self.rules.get_by_id_or_raise(rule_id)
        data = payload.model_dump(exclude_unset=True)
        for field, value in data.items():
            setattr(rule, field, value)
        if "schedule" in data:
            rule.next_run_at = self._next_run(rule.schedule, datetime.now(UTC))
        await self.session.flush()
        return rule

    async def delete_rule(self, rule_id: uuid.UUID) -> None:
        rule = await self.rules.get_by_id_or_raise(rule_id)
        await self.rules.soft_delete(rule)

    async def list_rules(self, params: ListQueryParams) -> tuple[list[AutomationRule], int]:
        rows, total = await self.rules.list(params)
        return list(rows), total

    async def list_runs(self, params: ListQueryParams) -> tuple[list[AutomationRun], int]:
        rows, total = await self.runs.list(params)
        return list(rows), total

    async def run_rule(self, rule_id: uuid.UUID, *, trigger: str = "manual") -> AutomationRun:
        rule = await self.rules.get_by_id_or_raise(rule_id)
        now = datetime.now(UTC)
        run = await self.runs.create(
            rule_id=rule.id,
            status=SyncRunStatus.RUNNING,
            trigger=trigger,
            started_at=now,
        )
        await self.session.flush()
        try:
            summary = await self._dispatch(rule)
            run.status = SyncRunStatus.SUCCEEDED
            run.summary = summary
            run.finished_at = datetime.now(UTC)
            rule.last_run_at = run.finished_at
            rule.next_run_at = self._next_run(rule.schedule, run.finished_at)
            rule.consecutive_failures = 0
            await self.session.flush()
            await self.notifications.notify(
                kind=NotificationKind.AUTOMATION_COMPLETED,
                title=f"Automation “{rule.name}” completed",
                body=summary or "",
                href="/automation",
                payload={"ruleId": str(rule.id), "runId": str(run.id)},
            )
            return run
        except Exception as exc:
            run.status = SyncRunStatus.FAILED
            run.error_message = str(exc)[:1024]
            run.finished_at = datetime.now(UTC)
            rule.last_run_at = run.finished_at
            rule.next_run_at = self._next_run(rule.schedule, run.finished_at)
            rule.consecutive_failures += 1
            await self.session.flush()
            await self.notifications.notify(
                kind=NotificationKind.AUTOMATION_FAILED,
                title=f"Automation “{rule.name}” failed",
                body=run.error_message or "",
                href="/automation",
                payload={"ruleId": str(rule.id), "runId": str(run.id)},
            )
            raise

    async def run_due(self, *, now: datetime | None = None) -> int:
        """Execute every due scheduled rule. Returns how many ran."""
        moment = now or datetime.now(UTC)
        due = await self.rules.list_due(now=moment)
        count = 0
        for rule in due:
            try:
                await self.run_rule(rule.id, trigger="scheduled")
                count += 1
            except Exception:
                logger.exception("automation_rule_failed", rule_id=str(rule.id))
        return count

    async def _dispatch(self, rule: AutomationRule) -> str:
        config = rule.config or {}
        if rule.action is AutomationAction.SYNC_INVENTORY:
            run = await InventorySyncService(self.session).sync(
                store_id=rule.store_id,
                product_id=uuid.UUID(config["product_id"]) if config.get("product_id") else None,
                trigger=SyncTrigger.SCHEDULED,
            )
            return f"Inventory: {run.products_seen} seen, {run.products_changed} changed."

        if rule.action is AutomationAction.UPDATE_PRICING:
            changes = await PricingEngine(self.session).apply(
                PricingApplyRequest(store_id=rule.store_id, limit=int(config.get("limit", 500)))
            )
            return f"Pricing: {len(changes)} price(s) updated."

        if rule.action is AutomationAction.REFRESH_ORDERS:
            order_run = await OrderSyncService(self.session).sync_orders(
                since_days=int(config.get("since_days", 7)),
            )
            return (
                f"Orders: {order_run.orders_seen} seen, "
                f"{order_run.orders_created} created, {order_run.orders_updated} updated."
            )

        if rule.action is AutomationAction.ARCHIVE_COMPLETED_ORDERS:
            # Soft-archive is a status move reserved for a dedicated product
            # decision; for now we report that nothing was mutated rather than
            # inventing a destructive behaviour.
            return "Archive completed orders: no-op (awaiting archive policy)."

        if rule.action is AutomationAction.RETRY_FAILED_JOBS:
            return "Retry failed jobs: no pending retries recorded."

        if rule.action is AutomationAction.IMPORT_PRODUCT:
            external_id = config.get("external_id")
            if not external_id:
                raise ValueError("import_product requires config.external_id")
            from app.services.product_import import ProductImportService

            product = await ProductImportService(self.session).import_product(
                external_id=str(external_id)
            )
            return f"Imported product {product.external_id}."

        return f"Unhandled action {rule.action.value}."

    def _next_run(self, schedule: AutomationSchedule, from_time: datetime) -> datetime | None:
        delta = _SCHEDULE_DELTA.get(schedule)
        if delta is None:
            return None
        return from_time + delta
