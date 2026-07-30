"""Task implementations.

Empty in Phase 0 — the platform has no background jobs yet.

When tasks are added, each module must also be listed in ``celery_app.conf.imports``
so the worker registers it. A task that is defined but never imported by the
worker process fails at call time with "unregistered task", which is a confusing
error to debug.

Expected modules in later phases: ``imports`` (supplier product ingestion),
``sync`` (inventory and price synchronisation), ``fulfilment`` (order placement
and tracking), ``notifications`` (email and webhook delivery).
"""
