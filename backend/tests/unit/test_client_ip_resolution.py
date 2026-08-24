"""Who is calling, and when a forwarding header may be believed.

Every per-IP control in this application — the general rate limit, the eBay
compliance budget, the login throttle — is only as good as this answer. Before
the acceptance fix all of them read ``X-Forwarded-For`` from any caller, so an
attacker reaching the service directly could rotate one header and be a fresh
client on every request.

The rule under test: **a forwarding header is evidence only when the immediate
socket peer is a configured trusted proxy.** Nothing else about a request — not
``CF-Ray``, not the presence of ``Forwarded``, not the ``Host`` — grants it.
"""

from __future__ import annotations

from typing import Any

import pytest
from starlette.requests import Request

from app.core.client_ip import UNKNOWN_CLIENT, client_ip_or_unknown, resolve_client_ip
from app.core.config import settings

pytestmark = pytest.mark.unit


def request_from(
    *, peer: str | None = "198.51.100.5", headers: dict[str, str] | None = None
) -> Request:
    """A Starlette request with a chosen socket peer and headers."""
    scope: dict[str, Any] = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
        "client": (peer, 12345) if peer else None,
    }
    return Request(scope)


@pytest.fixture
def trusting(monkeypatch: pytest.MonkeyPatch):
    """Configure a set of trusted proxy networks for one test."""

    def apply(*networks: str) -> None:
        monkeypatch.setattr(settings.security, "trusted_proxies", list(networks))

    return apply


class TestUntrustedCallers:
    def test_the_socket_peer_is_the_identity_by_default(self) -> None:
        """No configuration means no proxy, and no proxy means no header."""
        assert resolve_client_ip(request_from()) == "198.51.100.5"

    @pytest.mark.parametrize(
        "headers",
        [
            {"x-forwarded-for": "1.2.3.4"},
            {"x-forwarded-for": "1.2.3.4, 5.6.7.8"},
            {"cf-connecting-ip": "1.2.3.4"},
            {"cf-connecting-ip": "1.2.3.4", "cf-ray": "abc-LHR"},
            {"forwarded": "for=1.2.3.4"},
            {"x-real-ip": "1.2.3.4"},
        ],
    )
    def test_no_header_changes_the_identity_of_a_direct_caller(
        self, headers: dict[str, str]
    ) -> None:
        """Ignored entirely — not sanitised, not partially believed."""
        assert resolve_client_ip(request_from(headers=headers)) == "198.51.100.5"

    def test_rotating_forged_values_cannot_produce_two_identities(self) -> None:
        """The bypass, stated directly: same socket, same answer, every time."""
        identities = {
            resolve_client_ip(request_from(headers={"x-forwarded-for": f"10.0.0.{index}"}))
            for index in range(50)
        }
        assert identities == {"198.51.100.5"}

    def test_a_cf_ray_header_alone_grants_nothing(self) -> None:
        """Anyone can send one; it identifies a request, not a peer."""
        resolved = resolve_client_ip(
            request_from(headers={"cf-ray": "7a1b2c3d4e5f6789-LHR", "cf-connecting-ip": "9.9.9.9"})
        )
        assert resolved == "198.51.100.5"


class TestTrustedProxies:
    def test_a_configured_proxy_may_name_the_real_client(self, trusting: Any) -> None:
        trusting("198.51.100.5/32")
        resolved = resolve_client_ip(request_from(headers={"x-forwarded-for": "203.0.113.9"}))
        assert resolved == "203.0.113.9"

    def test_a_chain_is_walked_from_the_right(self, trusting: Any) -> None:
        """Two of our own proxies in front, one real client behind them.

        Taking ``[0]`` would work here by accident. The next test is the one
        that distinguishes a correct walk from that shortcut.
        """
        trusting("198.51.100.0/24", "192.0.2.0/24")
        resolved = resolve_client_ip(
            request_from(headers={"x-forwarded-for": "203.0.113.9, 192.0.2.1, 198.51.100.6"})
        )
        assert resolved == "203.0.113.9"

    def test_a_client_supplied_prefix_is_not_believed(self, trusting: Any) -> None:
        """The shortcut's failure mode.

        The real client sent its own ``X-Forwarded-For`` before reaching our
        proxy, so the chain begins with a value it chose. ``[0]`` would return
        the attacker's string; walking from the right returns the address our
        proxy observed.
        """
        trusting("198.51.100.0/24")
        resolved = resolve_client_ip(
            request_from(headers={"x-forwarded-for": "1.1.1.1, 203.0.113.9"})
        )
        assert resolved == "203.0.113.9", "the caller's own forged prefix was believed"

    def test_cf_connecting_ip_is_used_when_cloudflare_is_the_peer(self, trusting: Any) -> None:
        trusting("198.51.100.5/32")
        resolved = resolve_client_ip(request_from(headers={"cf-connecting-ip": "203.0.113.44"}))
        assert resolved == "203.0.113.44"

    def test_an_all_proxy_chain_falls_back_to_the_peer(self, trusting: Any) -> None:
        """Nothing in the chain is a client, so there is no client to name."""
        trusting("198.51.100.0/24")
        resolved = resolve_client_ip(
            request_from(headers={"x-forwarded-for": "198.51.100.6, 198.51.100.7"})
        )
        assert resolved == "198.51.100.5"


class TestAddressFamilies:
    def test_an_ipv6_peer_resolves(self) -> None:
        assert resolve_client_ip(request_from(peer="2001:db8::1")) == "2001:db8::1"

    def test_an_ipv6_proxy_may_forward_an_ipv4_client(self, trusting: Any) -> None:
        trusting("2001:db8::/32")
        resolved = resolve_client_ip(
            request_from(peer="2001:db8::1", headers={"x-forwarded-for": "203.0.113.9"})
        )
        assert resolved == "203.0.113.9"

    def test_an_ipv4_proxy_may_forward_an_ipv6_client(self, trusting: Any) -> None:
        trusting("198.51.100.0/24")
        resolved = resolve_client_ip(request_from(headers={"x-forwarded-for": "2001:db8:abcd::42"}))
        assert resolved == "2001:db8:abcd::42"

    def test_a_bracketed_ipv6_peer_is_normalised(self) -> None:
        assert resolve_client_ip(request_from(peer="[2001:db8::1]")) == "2001:db8::1"

    def test_one_address_yields_one_identity_however_it_is_spelled(self) -> None:
        """Otherwise a single client could occupy two rate-limit buckets."""
        assert resolve_client_ip(request_from(peer="2001:0db8:0000::0001")) == resolve_client_ip(
            request_from(peer="2001:db8::1")
        )


class TestMalformedInput:
    @pytest.mark.parametrize(
        "value",
        [
            "not-an-ip",
            "999.999.999.999",
            "'; DROP TABLE users; --",
            "10.0.0.1\nx-injected: yes",
            "a" * 500,
            "",
            ",,,",
        ],
    )
    def test_a_malformed_forwarding_header_never_becomes_an_identity(
        self, trusting: Any, value: str
    ) -> None:
        """Safely ignored, even from a trusted peer.

        The identity falls back to the peer rather than to whatever was sent,
        so nothing attacker-shaped can reach a Redis key.
        """
        trusting("198.51.100.0/24")
        resolved = resolve_client_ip(request_from(headers={"x-forwarded-for": value}))
        assert resolved == "198.51.100.5"

    def test_a_malformed_hop_stops_the_walk(self, trusting: Any) -> None:
        """Everything to the left of a bad entry is unverifiable."""
        trusting("198.51.100.0/24")
        resolved = resolve_client_ip(
            request_from(headers={"x-forwarded-for": "203.0.113.9, garbage, 198.51.100.6"})
        )
        assert resolved == "198.51.100.5"

    def test_an_absurdly_long_chain_is_refused(self, trusting: Any) -> None:
        trusting("198.51.100.0/24")
        chain = ", ".join(f"203.0.113.{index % 250}" for index in range(500))
        assert resolve_client_ip(request_from(headers={"x-forwarded-for": chain})) == "198.51.100.5"

    def test_a_missing_peer_is_reported_as_unknown_not_guessed(self) -> None:
        assert resolve_client_ip(request_from(peer=None)) is None
        assert client_ip_or_unknown(request_from(peer=None)) == UNKNOWN_CLIENT

    def test_a_malformed_trusted_proxy_configuration_is_a_loud_failure(self) -> None:
        """A silently dropped rule would collapse every client to one identity."""
        from app.core.config import SecuritySettings

        settings_with_bad_entry = SecuritySettings(trusted_proxies=["10.0.0.0/8", "not-a-network"])
        with pytest.raises(ValueError, match="SECURITY_TRUSTED_PROXIES"):
            _ = settings_with_bad_entry.trusted_proxy_networks


class TestRedisKeySafety:
    @pytest.mark.parametrize(
        "value",
        [
            "10.0.0.1 evil",
            "ratelimit:tenant:00000000-0000-0000-0000-000000000000",
            "*",
            "10.0.0.1\r\nSET foo bar",
        ],
    )
    def test_no_header_text_can_reach_a_rate_limit_key(self, trusting: Any, value: str) -> None:
        """Structural, not a blocklist.

        Every returned value is a parsed ``ip_address`` rendered back to text,
        so a key built from it is an address or the ``unknown`` constant and can
        be nothing else.
        """
        trusting("198.51.100.0/24")
        identity = client_ip_or_unknown(request_from(headers={"x-forwarded-for": value}))
        key = f"ratelimit:ip:{identity}"

        assert identity in {"198.51.100.5", UNKNOWN_CLIENT}
        for forbidden in (" ", "\r", "\n", "*", "evil"):
            assert forbidden not in identity, f"{forbidden!r} reached the key: {key!r}"

    def test_a_resolved_identity_is_always_an_address_or_the_constant(self, trusting: Any) -> None:
        from ipaddress import ip_address

        trusting("198.51.100.0/24")
        for headers in (
            {},
            {"x-forwarded-for": "203.0.113.9"},
            {"x-forwarded-for": "nonsense"},
            {"cf-connecting-ip": "2001:db8::5"},
        ):
            identity = client_ip_or_unknown(request_from(headers=headers))
            if identity != UNKNOWN_CLIENT:
                ip_address(identity)  # raises if it is anything else


class TestDefaultsAreSafe:
    def test_nothing_is_trusted_out_of_the_box(self) -> None:
        """The default that makes a misconfiguration visible rather than silent.

        With no trusted proxy configured a deployment that has just gained one
        sees every client collapse to the proxy's address — obvious, and
        fixable. The opposite default would let the internet forge identities
        with nothing to show for it.
        """
        from app.core.config import SecuritySettings

        assert SecuritySettings().trusted_proxies == []
        assert SecuritySettings().trusted_proxy_networks == ()

    def test_local_development_still_resolves_a_usable_identity(self) -> None:
        """A developer on loopback is a client, not an error."""
        assert resolve_client_ip(request_from(peer="127.0.0.1")) == "127.0.0.1"
