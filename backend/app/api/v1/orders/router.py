"""Order endpoints — reserved.

No endpoints in Phase 0. Orders carry money and customer personal data, which
makes them the most compliance-sensitive entity in the platform: they need an
audit trail, and they are the clearest case for never hard-deleting a row.

When implemented, this module owns order listing, detail, fulfilment status, and
tracking.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/orders", tags=["orders"])
