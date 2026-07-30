"""Store connection endpoints — reserved.

No endpoints in Phase 0. A "store" is a tenant's connection to a sales channel
or supplier — Shopify, WooCommerce, eBay, Etsy, TikTok Shop, AliExpress.

The design constraint worth recording now: credentials for these connections are
third-party secrets and must be encrypted at rest with a key held outside the
database, not stored as plaintext columns. That decision belongs to the phase
that implements it, but the requirement is noted here so it is not discovered
late.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/stores", tags=["stores"])
