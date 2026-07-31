"""Analytics dashboard — real metrics only."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import DbSession, RequireViewer
from app.schemas.analytics import AnalyticsDashboard
from app.services.analytics_service import AnalyticsService

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/dashboard", response_model=AnalyticsDashboard)
async def dashboard(
    session: DbSession,
    _authorized: RequireViewer,
    period_days: Annotated[int, Query(alias="periodDays", ge=1, le=365)] = 30,
) -> AnalyticsDashboard:
    return await AnalyticsService(session).dashboard(period_days=period_days)
