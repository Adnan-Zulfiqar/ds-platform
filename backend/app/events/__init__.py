"""Domain events.

Reserved. No events are published in Phase 0.

The package exists now to fix the seam. Several later features — audit logging,
webhook delivery to customer endpoints, cache invalidation, analytics rollups —
all need to react to "a product changed" without the code that changed the
product knowing about any of them. Routing those through domain events keeps
services from accumulating a list of unrelated side effects, which is the usual
way a service layer decays into an unmaintainable god object.

Intended shape: services publish immutable event objects; subscribers are
registered at startup; delivery is in-process initially and moves onto the
existing Celery infrastructure when a subscriber needs durability.
"""
