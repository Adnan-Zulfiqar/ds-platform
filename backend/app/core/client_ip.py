"""One validated answer to "who is calling?".

Before this module, five places each did the same thing:

    forwarded = request.headers.get("x-forwarded-for")
    return forwarded.split(",")[0].strip() if forwarded else request.client.host

That is correct **only** when something in front of the application overwrites
the header, and wrong the instant it does not. It was wrong here: nothing
validated the immediate peer, so any caller reaching the application directly
could send ``X-Forwarded-For: <anything>`` and be treated as that address. Every
per-IP control built on it — the general rate limit, the eBay compliance budget,
the login throttle — could be defeated by rotating a header, which is to say by
sending one extra line per request.

The rule this module enforces:

**A forwarding header is evidence only when the machine that sent it is one we
put there.** ``TRUSTED_PROXIES`` is an explicit list of CIDRs. If the immediate
socket peer is not inside one, every forwarding header is ignored entirely — not
sanitised, not partially believed, ignored — and the socket address is the
identity.

When the peer *is* trusted, the chain is walked from the right: each hop that is
itself a trusted proxy is skipped, and the first address that is not becomes the
client. Taking ``X-Forwarded-For[0]`` instead — the usual shortcut — reads the
value the *original caller* put there, which is attacker-controlled even behind
a correct proxy.

Nothing attacker-supplied ever leaves this module. Every returned value is a
parsed ``ip_address`` rendered back to text, so a Redis key built from it cannot
contain arbitrary header content.
"""

from __future__ import annotations

from ipaddress import AddressValueError, ip_address
from typing import Final

from starlette.requests import Request

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

#: Used when there is no socket peer at all — an in-process ASGI call, or a
#: transport that does not report one. A constant, so it can never be confused
#: with a real address.
UNKNOWN_CLIENT: Final = "unknown"

#: Headers a trusted proxy may use to name the real client, most specific first.
#: ``CF-Connecting-IP`` is Cloudflare's single-value header and is unambiguous
#: when Cloudflare is the peer; ``X-Forwarded-For`` is the general chain.
#:
#: The mere *presence* of any of these means nothing. ``CF-Ray`` is deliberately
#: absent: it identifies a Cloudflare request, and anyone can send one.
_CLIENT_HEADERS: Final = ("cf-connecting-ip", "x-forwarded-for")

#: A forwarding chain longer than this is not a deployment, it is an attempt to
#: make the walk expensive.
_MAX_CHAIN_HOPS: Final = 32


def resolve_client_ip(request: Request) -> str | None:
    """The caller's address, or ``None`` when it cannot be established.

    Returns ``None`` rather than a placeholder so callers that treat "unknown"
    as a distinct case — the login throttle drops the IP dimension entirely —
    keep doing so.
    """
    peer = _peer_address(request)
    if peer is None:
        return None
    if not _is_trusted_proxy(peer):
        # The header is ignored, not cleaned. A caller that is not a proxy we
        # installed has no standing to tell us who it is forwarding for.
        return peer
    forwarded = _client_from_chain(request)
    return forwarded or peer


def client_ip_or_unknown(request: Request) -> str:
    """``resolve_client_ip`` with a safe constant instead of ``None``.

    For rate-limit keys, where a missing identity still needs a bucket. Sharing
    one bucket across callers with no reportable address is the conservative
    choice: it throttles more, never less.
    """
    return resolve_client_ip(request) or UNKNOWN_CLIENT


def _peer_address(request: Request) -> str | None:
    """The immediate socket peer, normalised, or ``None``.

    Parsed and re-rendered rather than passed through: it comes from the ASGI
    server rather than the network, but normalising here means one spelling of
    an address cannot become two rate-limit buckets.
    """
    client = request.client
    if client is None or not client.host:
        return None
    return _normalise(client.host)


def _normalise(value: str) -> str | None:
    candidate = value.strip()
    if not candidate:
        return None
    # An IPv6 literal may arrive bracketed, and a peer may carry a port.
    if candidate.startswith("[") and "]" in candidate:
        candidate = candidate[1 : candidate.index("]")]
    try:
        return str(ip_address(candidate))
    except (ValueError, AddressValueError):
        return None


def _is_trusted_proxy(peer: str) -> bool:
    """Whether ``peer`` is inside a configured trusted network.

    Empty configuration means *nothing* is trusted, which is the correct
    default: an application with no proxy in front of it must never believe a
    forwarding header, and one that has just gained a proxy should fail closed
    until it is configured rather than silently trusting the internet.
    """
    networks = settings.security.trusted_proxy_networks
    if not networks:
        return False
    try:
        address = ip_address(peer)
    except ValueError:  # pragma: no cover - peer is already normalised
        return False
    return any(address in network for network in networks)


def _client_from_chain(request: Request) -> str | None:
    """Walk the forwarding chain and return the first untrusted hop.

    Right to left: the rightmost entry was added by our own proxy and is the
    most trustworthy, the leftmost by whoever spoke first and is the least. Each
    trusted hop is skipped; the first address that is not one of ours is the
    client. If every hop is a trusted proxy, there is no client to name and the
    caller falls back to the peer.
    """
    single = request.headers.get("cf-connecting-ip")
    if single:
        # Cloudflare sets exactly one address, and only reaches here when
        # Cloudflare itself is the trusted peer.
        return _normalise(single)

    raw = request.headers.get("x-forwarded-for")
    if not raw:
        return None

    hops = [hop.strip() for hop in raw.split(",") if hop.strip()]
    if len(hops) > _MAX_CHAIN_HOPS:
        logger.warning("forwarded_chain_too_long", hops=len(hops))
        return None

    for hop in reversed(hops):
        address = _normalise(hop)
        if address is None:
            # A malformed entry poisons the chain: everything to its left is
            # unverifiable, so the walk stops rather than guessing.
            logger.warning("forwarded_chain_malformed")
            return None
        if not _is_trusted_proxy(address):
            return address
    return None


__all__ = ["UNKNOWN_CLIENT", "client_ip_or_unknown", "resolve_client_ip"]
