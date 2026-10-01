"""Review finding B-3 — residual SSRF and availability cases for ImageFetcher.

Everything in ``test_image_fetch.py`` still holds; these add the cases the
review found open: NAT64 and IPv4-compatible addresses that Python reports as
global, a slow-drip body with no whole-fetch deadline, and a blocking resolver
on the event loop. No socket leaves the process.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import AsyncIterator, Sequence

import httpx
import pytest

from app.ai import image_fetch
from app.ai.image_fetch import (
    PNG_MAGIC,
    ImageFetcher,
    ImageFetchNotGlobalAddress,
    ImageFetchTimeout,
    choose_connectable,
)

pytestmark = pytest.mark.unit

PUBLIC_V4 = "8.8.8.8"


def _ok_png(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, headers={"Content-Type": "image/png"}, content=PNG_MAGIC + b"\x00")


def _fetcher(resolved: Sequence[str], transport: httpx.AsyncBaseTransport) -> ImageFetcher:
    return ImageFetcher(resolver=lambda _host: list(resolved), transport=transport)


class TestNat64AndCompatibleAddresses:
    @pytest.mark.parametrize(
        "raw",
        [
            "64:ff9b::a9fe:a9fe",  # NAT64 → 169.254.169.254 (cloud metadata)
            "64:ff9b::7f00:1",  # NAT64 → 127.0.0.1
            "64:ff9b::a00:1",  # NAT64 → 10.0.0.1
            "64:ff9b::6440:1",  # NAT64 → 100.64.0.1 (CGNAT)
            "::127.0.0.1",  # IPv4-compatible loopback
            "::8.8.8.8",  # IPv4-compatible, even when the IPv4 is public
            "2002:a9fe:a9fe::1",  # 6to4 → metadata (already refused; pinned)
        ],
    )
    def test_python_global_but_unsafe_targets_are_refused(self, raw: str) -> None:
        with pytest.raises(ImageFetchNotGlobalAddress):
            choose_connectable([raw])

    def test_nat64_to_a_public_address_is_allowed_and_keeps_the_v6_target(self) -> None:
        chosen = choose_connectable(["64:ff9b::808:808"])
        assert str(chosen) == "64:ff9b::808:808"

    def test_one_unsafe_nat64_record_rejects_the_whole_host(self) -> None:
        with pytest.raises(ImageFetchNotGlobalAddress):
            choose_connectable([PUBLIC_V4, "64:ff9b::a9fe:a9fe"])

    async def test_a_nat64_literal_url_is_refused_before_any_request(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return _ok_png(request)

        fetcher = _fetcher([PUBLIC_V4], httpx.MockTransport(handler))
        with pytest.raises(ImageFetchNotGlobalAddress):
            await fetcher.fetch("https://[64:ff9b::a9fe:a9fe]/a.png")
        assert requests == []

    async def test_a_redirect_to_a_nat64_metadata_host_is_refused(self) -> None:
        responses = iter(
            [
                httpx.Response(302, headers={"Location": "https://inner.test/x.png"}),
                httpx.Response(200, headers={"Content-Type": "image/png"}, content=PNG_MAGIC),
            ]
        )
        fetcher = ImageFetcher(
            resolver=lambda host: ["64:ff9b::a9fe:a9fe"] if host == "inner.test" else [PUBLIC_V4],
            transport=httpx.MockTransport(lambda _r: next(responses)),
        )
        with pytest.raises(ImageFetchNotGlobalAddress):
            await fetcher.fetch("https://outer.test/a.png")


class _DripStream(httpx.AsyncByteStream):
    """One small chunk at a time, each well inside the per-read timeout."""

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield PNG_MAGIC
        while True:
            await asyncio.sleep(0.05)
            yield b"\x00"


class TestWholeFetchDeadline:
    async def test_a_slow_drip_body_hits_the_total_deadline(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(image_fetch, "TOTAL_DEADLINE_SECONDS", 0.3)

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, headers={"Content-Type": "image/png"}, stream=_DripStream())

        fetcher = _fetcher([PUBLIC_V4], httpx.MockTransport(handler))
        started = time.monotonic()
        with pytest.raises(ImageFetchTimeout):
            await fetcher.fetch("https://example.test/a.png")
        assert time.monotonic() - started < 3

    async def test_a_hanging_resolver_hits_the_total_deadline(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(image_fetch, "TOTAL_DEADLINE_SECONDS", 0.3)
        release = threading.Event()

        def resolver(_host: str) -> Sequence[str]:
            release.wait(5)
            return [PUBLIC_V4]

        fetcher = ImageFetcher(resolver=resolver, transport=httpx.MockTransport(_ok_png))
        try:
            with pytest.raises(ImageFetchTimeout):
                await fetcher.fetch("https://example.test/a.png")
        finally:
            release.set()

    def test_the_default_budget_is_bounded(self) -> None:
        assert 0 < image_fetch.TOTAL_DEADLINE_SECONDS <= 60


class TestResolverRunsOffTheEventLoop:
    async def test_a_slow_resolver_does_not_block_other_coroutines(self) -> None:
        loop_thread = threading.get_ident()
        resolver_threads: list[int] = []

        def resolver(_host: str) -> Sequence[str]:
            resolver_threads.append(threading.get_ident())
            time.sleep(0.3)  # a blocking getaddrinfo
            return [PUBLIC_V4]

        ticks = 0

        async def heartbeat() -> None:
            nonlocal ticks
            for _ in range(5):
                await asyncio.sleep(0.02)
                ticks += 1

        fetcher = ImageFetcher(resolver=resolver, transport=httpx.MockTransport(_ok_png))
        await asyncio.gather(fetcher.fetch("https://example.test/a.png"), heartbeat())

        assert resolver_threads and resolver_threads[0] != loop_thread
        assert ticks == 5
