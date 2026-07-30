"""Background task implementations — an entry point into the domain.

Empty in Phase 0; the platform has no background jobs yet.

**Why this sits beside ``api`` rather than inside ``workers``.** Tasks are entry
points, architecturally symmetric with HTTP routes: both are adapters that
translate an external trigger — a request, a queued message — into a service
call, and neither is imported by anything beneath it. ``app.workers`` holds the
infrastructure that *runs* tasks (the Celery application, the base task class,
the retry policy); this package holds the work itself.

That distinction matters for this platform in particular, where inventory sync,
price sync, and order fulfilment are all background work. Those are core product
capabilities, not a subsystem off to the side.

Every task module must also be listed in ``celery_app.conf.imports``. A task
that is defined but never imported by the worker process fails at call time with
"unregistered task", which is a confusing error to debug.

Expected modules in later phases: ``imports`` (supplier product ingestion),
``sync`` (inventory and price synchronisation), ``fulfilment`` (order placement
and tracking), ``notifications`` (email and webhook delivery).
"""
