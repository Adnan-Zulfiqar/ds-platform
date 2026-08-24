"""eBay integration package.

EBAY-C0 is compliance only: the Marketplace Account Deletion/Closure endpoint
and the credentials it needs. There is no eBay OAuth, no listing, no inventory
and no order code here — those are EBAY-C1 onwards, and
``docs/ebay/MASTER_EBAY_ROADMAP.md`` records the order and the release guard
that stops eBay user data being stored before the deletion processor knows how
to erase it.
"""

from __future__ import annotations
