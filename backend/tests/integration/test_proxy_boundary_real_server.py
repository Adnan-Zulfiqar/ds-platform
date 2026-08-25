"""The proxy boundary, verified through a real uvicorn process.

Every other test of ``app.core.client_ip`` drives the ASGI application
directly. That is the right way to test the resolver, and it is exactly why the
defect this module exists for stayed invisible: the bug was never in the
application. Uvicorn's ``ProxyHeadersMiddleware`` is enabled by default and
rewrites ``scope["client"]`` from ``X-Forwarded-For`` whenever the socket peer
is inside its own trusted list — which defaults to ``127.0.0.1``, which is
exactly where cloudflared connects from. By the time the resolver ran, the
"socket peer" it was handed was already a value the caller had chosen.

Measured against the repository's real launch command before the fix: 700
requests rotating a forged ``X-Forwarded-For`` produced **zero** 429s and 650
separate rate-limit buckets, where 700 requests from one address produced 53.

So these tests start the server the way the repository actually starts it, over
a real socket, and assert on behaviour a caller can observe: whether the quota
can be escaped. A test that could pass without a server process would not have
caught this.

They are slow — each launches a process — and are marked ``integration``
accordingly.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path

import httpx
import pytest

from tests.unit.test_server_launch_surfaces import _REQUIRED_FLAG

pytestmark = pytest.mark.integration

_BACKEND_ROOT = Path(__file__).resolve().parents[2]

#: A quota small enough that a bypass shows up in a handful of requests. The
#: limiter itself is exercised at its real settings elsewhere; what is under
#: test here is *whose* counter is incremented.
_LIMIT = 5

#: Not routed. The rate-limit middleware runs before routing, so this exercises
#: the quota exactly while touching no handler, no database row and no provider.
_PROBE_PATH = "/api/v1/proxy-boundary-probe"

_LOOPBACK_TRUSTED = "127.0.0.1/32,::1/128"

#: Imported rather than repeated: the launch-surface guard enforces this exact
#: flag across every production command, and these tests must exercise the same
#: one. If it is ever renamed, both fail together instead of silently diverging.
REQUIRED_UVICORN_FLAG = _REQUIRED_FLAG


def _free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _has_non_loopback_address() -> str | None:
    """A local address that is not loopback, if this machine has one.

    Used to prove the peer check itself, which loopback traffic cannot show:
    with proxy headers disabled the header is ignored either way, so only a
    genuinely different peer distinguishes "ignored because untrusted" from
    "ignored because never parsed".
    """
    for family, _, _, _, address in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
        if family is socket.AF_INET and not address[0].startswith("127."):
            return str(address[0])
    return None


@contextmanager
def real_server(
    *, trusted_proxies: str = "", host: str = "127.0.0.1", harden: bool = True
) -> Iterator[str]:
    """Start the API the way the repository starts it, and yield its base URL.

    The argument vector mirrors the production launch surfaces, taking the flag
    from the same constant the launch-surface guard enforces — so the two
    cannot drift apart. ``harden=False`` starts an *unhardened* server on
    purpose; only ``TestTheFlagIsWhatDoesTheWork`` uses it.
    """
    port = _free_port()
    command = [
        sys.executable,
        "-m",
        "uvicorn",
        "app.main:app",
        *([REQUIRED_UVICORN_FLAG] if harden else []),
        "--host",
        host,
        "--port",
        str(port),
        "--log-level",
        "warning",
    ]
    # Explicit variables rather than a file: environment beats ``.env`` in
    # pydantic-settings, so a developer's real credentials cannot change what
    # this asserts.
    env = {
        **os.environ,
        "SECURITY_TRUSTED_PROXIES": trusted_proxies,
        "SECURITY_RATE_LIMIT_ENABLED": "true",
        "SECURITY_RATE_LIMIT_REQUESTS": str(_LIMIT),
        "SECURITY_RATE_LIMIT_WINDOW_SECONDS": "60",
    }
    # A file, not a pipe. The server logs one line per request; an unread pipe
    # fills its OS buffer and the server then blocks on write, which looks
    # exactly like the application hanging.
    log = tempfile.TemporaryFile()
    process = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
        command,
        cwd=_BACKEND_ROOT,
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
    )

    def _log_text() -> str:
        log.seek(0)
        return log.read().decode("utf-8", "replace")

    base = f"http://{'127.0.0.1' if host == '0.0.0.0' else host}:{port}"  # noqa: S104
    try:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if process.poll() is not None:
                pytest.fail(f"the server exited during startup:\n{_log_text()}")
            try:
                if httpx.get(f"{base}/health/live", timeout=1.0).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.5)
        else:  # pragma: no cover - only on a machine that cannot start the app
            pytest.fail(f"the server never became live:\n{_log_text()}")
        yield base
    finally:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:  # pragma: no cover - defensive
            process.kill()
        log.close()


def probe(base: str, headers: dict[str, str] | None = None, *, path: str = _PROBE_PATH) -> int:
    with httpx.Client(timeout=10.0) as client:
        return client.get(f"{base}{path}", headers=headers or {}).status_code


def refusals(codes: list[int]) -> int:
    return sum(1 for code in codes if code == 429)


@pytest.fixture(scope="module")
def untrusting_server() -> Iterator[str]:
    """The shipped default: nothing is a trusted proxy."""
    with real_server(trusted_proxies="") as base:
        yield base


@pytest.fixture(scope="module")
def loopback_trusting_server() -> Iterator[str]:
    """The Cloudflare Tunnel deployment contract, as an explicit setting."""
    with real_server(trusted_proxies=_LOOPBACK_TRUSTED) as base:
        yield base


class TestBlankTrustedProxies:
    """A. Nothing configured — the socket peer is the only identity."""

    def test_forty_rotating_forwarded_for_values_share_one_quota(
        self, untrusting_server: str
    ) -> None:
        """The bypass, stated as the thing an attacker actually wants.

        Before the fix this produced 40 fresh quotas and no refusals at all.
        """
        codes = [
            probe(untrusting_server, {"X-Forwarded-For": f"203.0.113.{index}"})
            for index in range(1, 41)
        ]
        assert refusals(codes) >= 40 - _LIMIT, (
            "rotating X-Forwarded-For minted new quotas: only "
            f"{refusals(codes)} of 40 requests were refused"
        )

    def test_forty_rotating_cf_connecting_ip_values_share_one_quota(
        self, untrusting_server: str
    ) -> None:
        codes = [
            probe(untrusting_server, {"CF-Connecting-IP": f"198.18.0.{index}"})
            for index in range(1, 41)
        ]
        assert refusals(codes) >= 40 - _LIMIT

    def test_a_mixture_of_forwarding_headers_changes_nothing(self, untrusting_server: str) -> None:
        """``CF-Ray`` in particular grants nothing — anyone can send one."""
        codes = [
            probe(
                untrusting_server,
                {
                    "X-Forwarded-For": f"192.0.2.{index}, 198.51.100.{index}",
                    "CF-Connecting-IP": f"203.0.113.{index}",
                    "CF-Ray": f"7a1b2c3d4e5f678{index}-LHR",
                    "Forwarded": f"for=10.0.0.{index}",
                    "X-Real-IP": f"172.16.0.{index}",
                },
            )
            for index in range(1, 41)
        ]
        assert refusals(codes) >= 40 - _LIMIT

    def test_the_quota_is_reachable_at_all(self, untrusting_server: str) -> None:
        """The control on the controls.

        If the limiter were simply off, every assertion above would pass for the
        wrong reason. This shows the quota is real and that a caller who does
        not forge anything hits it too.
        """
        codes = [probe(untrusting_server) for _ in range(_LIMIT + 10)]
        assert 429 in codes, "the limiter is not active; the other tests prove nothing"


class TestExplicitLoopbackTrustedProxy:
    """B. The Cloudflare Tunnel contract — loopback trusted, and only loopback."""

    def test_a_forwarded_client_is_believed_when_loopback_is_trusted(
        self, loopback_trusting_server: str
    ) -> None:
        """Uvicorn still hands over the true peer; the application decides."""
        codes = [
            probe(loopback_trusting_server, {"X-Forwarded-For": "203.0.113.240"})
            for _ in range(_LIMIT + 5)
        ]
        assert 429 in codes, "the forwarded client got no quota of its own"

    def test_two_real_clients_get_separate_quotas(self, loopback_trusting_server: str) -> None:
        """The point of trusting a proxy: per-client limits behind it.

        One client is exhausted; a different client must still be served, or
        the deployment has per-deployment limits rather than per-client ones.
        """
        for _ in range(_LIMIT + 3):
            probe(loopback_trusting_server, {"X-Forwarded-For": "203.0.113.241"})
        assert probe(loopback_trusting_server, {"X-Forwarded-For": "203.0.113.241"}) == 429
        assert probe(loopback_trusting_server, {"X-Forwarded-For": "203.0.113.242"}) != 429

    def test_an_attacker_supplied_prefix_does_not_win(self, loopback_trusting_server: str) -> None:
        """Right-to-left, proven through the socket.

        The caller prepends its own value before reaching the proxy, so the
        chain starts with a string it chose. Taking ``[0]`` would give each
        forged prefix a fresh quota; walking from the right counts them all
        against the address the proxy observed.
        """
        for index in range(_LIMIT + 3):
            probe(
                loopback_trusting_server,
                {"X-Forwarded-For": f"1.1.1.{index}, 203.0.113.243"},
            )
        assert (
            probe(
                loopback_trusting_server,
                {"X-Forwarded-For": "9.9.9.9, 203.0.113.243"},
            )
            == 429
        ), "a forged leftmost prefix minted a new quota"

    def test_a_malformed_chain_falls_back_to_the_peer(self, loopback_trusting_server: str) -> None:
        """Fails closed: unusable input never becomes a fresh identity."""
        codes = [
            probe(loopback_trusting_server, {"X-Forwarded-For": value})
            for value in (
                "not-an-ip",
                "999.999.999.999",
                "'; DROP TABLE users; --",
                "",
                ",,,",
                "10.0.0.1 evil",
                "*",
            )
            for _ in range(3)
        ]
        assert 429 in codes, "malformed values were each treated as a new client"

    def test_no_header_text_reaches_a_rate_limit_key(self, loopback_trusting_server: str) -> None:
        """Structural, and checked against the real store.

        Every identity is a parsed address rendered back to text, so a key can
        be an address or the ``unknown`` constant and nothing else.
        """
        from redis import Redis

        from app.core.config import settings

        probe(loopback_trusting_server, {"X-Forwarded-For": "10.0.0.1 evil"})
        probe(loopback_trusting_server, {"X-Forwarded-For": "*"})
        probe(loopback_trusting_server, {"X-Forwarded-For": "203.0.113.244"})

        client: Redis = Redis(
            host=settings.redis.host,
            port=settings.redis.port,
            db=settings.redis.rate_limit_db,
            protocol=2,
            decode_responses=True,
        )
        try:
            keys = [str(key) for key in client.scan_iter(match="ratelimit:*")]
        finally:
            client.close()

        assert keys, "no rate-limit keys were written; this test proves nothing"
        for key in keys:
            identity = key.split(":", 2)[-1]
            for forbidden in (" ", "\r", "\n", "*", "evil", "DROP"):
                assert forbidden not in identity, f"{forbidden!r} reached a key: {key!r}"


class TestNonLoopbackPeer:
    """C. A genuinely different peer — the trust check itself."""

    def test_forwarding_headers_never_override_a_non_loopback_peer(self) -> None:
        address = _has_non_loopback_address()
        if address is None:
            pytest.skip(
                "this machine reports no non-loopback IPv4 address, so a "
                "different socket peer cannot be produced here"
            )
        # Loopback is trusted, and the caller is not loopback: the header must
        # be ignored, and every forged value must share the caller's quota.
        with real_server(trusted_proxies=_LOOPBACK_TRUSTED, host="0.0.0.0") as base:  # noqa: S104
            remote = base.replace("127.0.0.1", address)
            codes = [
                probe(remote, {"X-Forwarded-For": f"203.0.113.{index}"})
                for index in range(1, _LIMIT + 10)
            ]
            assert 429 in codes, "a non-proxy caller changed its own identity by sending a header"


class TestTheFlagIsWhatDoesTheWork:
    """The causality, pinned so it cannot quietly stop being true.

    Everything above passes with the flag present. That alone does not show the
    flag is the reason — a test can pass because the property holds for some
    other cause, and then keep passing after the cause disappears. So this
    starts the same application with the flag removed and requires the bypass
    to come back.

    It is the red-before evidence, kept executable.
    """

    def test_removing_the_flag_restores_the_bypass(self) -> None:
        with real_server(trusted_proxies="", harden=False) as base:
            codes = [
                probe(base, {"X-Forwarded-For": f"203.0.113.{index}"})
                for index in range(1, _LIMIT + 20)
            ]
        assert refusals(codes) == 0, (
            "an unhardened server refused a rotating-header caller. Either "
            "uvicorn changed its default (see "
            "tests/unit/test_server_launch_surfaces.py::"
            "TestUvicornStillBehavesAsAssumed) or something else now blocks the "
            "bypass — in both cases the reasoning behind --no-proxy-headers "
            "needs re-reading before this assertion is relaxed."
        )
