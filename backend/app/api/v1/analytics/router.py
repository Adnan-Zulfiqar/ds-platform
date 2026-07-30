"""Analytics endpoints — reserved.

No endpoints in Phase 0.

One architectural note recorded early because it shapes the schema: analytics
queries aggregate across large row counts and must not contend with the
transactional workload. The intended path is a read replica or pre-aggregated
rollup tables rather than ad-hoc ``GROUP BY`` over the live product and order
tables. The session dependency in ``app.api.deps`` is the place a replica-bound
session would be introduced.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/analytics", tags=["analytics"])
