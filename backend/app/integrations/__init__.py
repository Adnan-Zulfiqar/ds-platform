"""Third-party marketplace and supplier integrations.

Each provider gets its own subpackage with the same internal shape:
``client`` (HTTP), ``auth`` (signing and OAuth), ``exceptions``, ``schemas``,
and ``service`` (orchestration).

The consistency is the point. A developer who has read one integration can find
their way around the next, and the shared concerns — outbound rate limiting,
backoff — live here rather than being reimplemented per provider.

**Nothing in this package may be imported by a router directly.** Endpoints
depend on the provider's ``service``, which is what keeps AliExpress's error
vocabulary and signing scheme out of the HTTP layer.
"""
