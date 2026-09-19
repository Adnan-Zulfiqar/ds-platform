"""SSRF and streaming contract for `ImageFetcher`.

No test opens a real socket to the public internet. Resolver and httpx
transport are injected doubles.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator, Sequence

import httpx
import pytest

from app.ai.image_fetch import (
    JPEG_MAGIC,
    MAX_BODY,
    PNG_MAGIC,
    ImageFetchBadContentType,
    ImageFetchBadMagic,
    ImageFetchDisallowedScheme,
    ImageFetchDnsFailure,
    ImageFetcher,
    ImageFetchHostnameBlocked,
    ImageFetchHttpError,
    ImageFetchInvalidUrl,
    ImageFetchNotGlobalAddress,
    ImageFetchTimeout,
    ImageFetchTooLarge,
    ImageFetchTooManyRedirects,
    ImageFetchZoneId,
    choose_connectable,
    normalize_ip,
)

pytestmark = pytest.mark.unit

TINY_PNG = PNG_MAGIC + b"\x00"
TINY_JPEG = JPEG_MAGIC + b"\x00"
PUBLIC_V4 = "8.8.8.8"
PUBLIC_V6 = "2001:4860:4860::8888"


class RecordingByteStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks
        self.chunks_yielded = 0
        self.iterated = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        self.iterated = True
        for chunk in self._chunks:
            self.chunks_yielded += 1
            yield chunk


class RecordingClient(httpx.AsyncClient):
    constructed: list[RecordingClient]
    closed_clients: list[RecordingClient]
    methods: list[str]
    requests: list[httpx.Request]
    constructor_kwargs: dict[str, object]

    def __init__(self, **kwargs: object) -> None:
        RecordingClient.constructor_kwargs = dict(kwargs)
        RecordingClient.constructed.append(self)
        super().__init__(**kwargs)  # type: ignore[arg-type]

    async def aclose(self) -> None:
        await super().aclose()
        RecordingClient.closed_clients.append(self)

    def stream(self, method: str, url: httpx.URL | str, **kwargs: object) -> object:
        RecordingClient.methods.append("stream")
        return super().stream(method, url, **kwargs)

    async def request(self, method: str, url: httpx.URL | str, **kwargs: object) -> httpx.Response:
        RecordingClient.methods.append("request")
        return await super().request(method, url, **kwargs)

    async def get(self, url: httpx.URL | str, **kwargs: object) -> httpx.Response:
        RecordingClient.methods.append("get")
        return await super().get(url, **kwargs)


def _reset_client_records() -> None:
    RecordingClient.constructed = []
    RecordingClient.closed_clients = []
    RecordingClient.methods = []
    RecordingClient.requests = []
    RecordingClient.constructor_kwargs = {}


def _client_factory(**kwargs: object) -> RecordingClient:
    return RecordingClient(**kwargs)


def _transport(
    handler: httpx.MockTransport | None = None,
    *,
    on_request: list[httpx.Request] | None = None,
    respond: httpx.Response | None = None,
    responders: Iterator[httpx.Response] | None = None,
) -> httpx.MockTransport:
    if handler is not None:
        return handler

    def _handle(request: httpx.Request) -> httpx.Response:
        if on_request is not None:
            on_request.append(request)
        if responders is not None:
            return next(responders)
        assert respond is not None
        return respond

    return httpx.MockTransport(_handle)


def _fetcher(
    *,
    resolved: Sequence[str] | dict[str, Sequence[str]] | None = None,
    resolve_calls: list[str] | None = None,
    transport: httpx.MockTransport,
) -> ImageFetcher:
    def resolver(hostname: str) -> Sequence[str]:
        if resolve_calls is not None:
            resolve_calls.append(hostname)
        if isinstance(resolved, dict):
            return resolved[hostname]
        return list(resolved or [PUBLIC_V4])

    return ImageFetcher(
        resolver=resolver,
        transport=transport,
        client_factory=_client_factory,
    )


@pytest.fixture(autouse=True)
def _reset_records() -> Iterator[None]:
    _reset_client_records()
    yield
    _reset_client_records()


class TestAddressPolicy:
    def test_cgnat_is_rejected(self) -> None:
        with pytest.raises(ImageFetchNotGlobalAddress):
            choose_connectable(["100.64.0.1"])

    @pytest.mark.parametrize(
        "raw",
        [
            "127.0.0.1",
            "10.0.0.1",
            "169.254.169.254",
            "192.168.1.1",
            "172.16.0.1",
            "192.0.2.1",
            "::1",
            "fc00::1",
            "fe80::1",
            "::ffff:10.0.0.1",
        ],
    )
    def test_non_global_addresses_are_rejected(self, raw: str) -> None:
        with pytest.raises(ImageFetchNotGlobalAddress):
            choose_connectable([raw])

    def test_public_ipv4_fixture_is_allowed(self) -> None:
        chosen = choose_connectable([PUBLIC_V4])
        assert str(chosen) == PUBLIC_V4
        assert chosen.is_global is True

    def test_mapped_public_ipv4_is_unwrapped(self) -> None:
        chosen = choose_connectable(["::ffff:8.8.8.8"])
        assert str(chosen) == PUBLIC_V4

    def test_mixed_public_and_loopback_rejects_the_host(self) -> None:
        with pytest.raises(ImageFetchNotGlobalAddress):
            choose_connectable([PUBLIC_V4, "127.0.0.1"])

    def test_normalize_rejects_unparseable(self) -> None:
        with pytest.raises(ImageFetchNotGlobalAddress):
            normalize_ip("not-an-ip")


class TestHopMechanics:
    async def test_connects_to_resolved_ip_with_logical_host_and_sni(self) -> None:
        seen: list[httpx.Request] = []
        fetcher = _fetcher(
            resolved=[PUBLIC_V4],
            transport=_transport(
                on_request=seen,
                respond=httpx.Response(
                    200,
                    headers={"Content-Type": "image/png"},
                    content=TINY_PNG,
                ),
            ),
        )

        body = await fetcher.fetch("https://example.test/a.png")

        assert body == TINY_PNG
        assert seen[0].url.host == PUBLIC_V4
        assert seen[0].headers["host"] == "example.test"
        assert seen[0].extensions["sni_hostname"] == "example.test"
        assert RecordingClient.constructor_kwargs["trust_env"] is False
        assert RecordingClient.constructor_kwargs["follow_redirects"] is False
        assert RecordingClient.constructor_kwargs["verify"] is True
        assert RecordingClient.methods == ["stream"]
        assert "request" not in RecordingClient.methods
        assert "get" not in RecordingClient.methods
        assert all(client.is_closed for client in RecordingClient.constructed)

    async def test_original_hostname_is_never_the_connection_target(self) -> None:
        seen: list[httpx.Request] = []
        fetcher = _fetcher(
            resolved=[PUBLIC_V4],
            transport=_transport(
                on_request=seen,
                respond=httpx.Response(
                    200, headers={"Content-Type": "image/jpeg"}, content=TINY_JPEG
                ),
            ),
        )

        await fetcher.fetch("https://example.test/a.jpg")

        assert seen[0].url.host != "example.test"
        assert seen[0].url.host == PUBLIC_V4

    async def test_ipv6_connection_url_is_bracketed(self) -> None:
        seen: list[httpx.Request] = []
        fetcher = _fetcher(
            resolved=[PUBLIC_V6],
            transport=_transport(
                on_request=seen,
                respond=httpx.Response(
                    200, headers={"Content-Type": "image/png"}, content=TINY_PNG
                ),
            ),
        )

        await fetcher.fetch("https://example.test/a.png")

        assert seen[0].url.host == PUBLIC_V6
        assert str(seen[0].url).startswith(f"https://[{PUBLIC_V6}]/")

    async def test_http_scheme_is_rejected(self) -> None:
        fetcher = _fetcher(transport=_transport(respond=httpx.Response(200)))
        with pytest.raises(ImageFetchDisallowedScheme):
            await fetcher.fetch("http://example.test/a.png")

    @pytest.mark.parametrize(
        "url",
        [
            "https://localhost/a.png",
            "https://localhost./a.png",
            "https://metadata.google.internal/a.png",
            "https://metadata.internal/a.png",
        ],
    )
    async def test_blocked_hostnames_are_rejected(self, url: str) -> None:
        fetcher = _fetcher(transport=_transport(respond=httpx.Response(200)))
        with pytest.raises(ImageFetchHostnameBlocked):
            await fetcher.fetch(url)

    async def test_loopback_literal_is_not_global(self) -> None:
        fetcher = _fetcher(transport=_transport(respond=httpx.Response(200)))
        with pytest.raises(ImageFetchNotGlobalAddress):
            await fetcher.fetch("https://127.0.0.1/a.png")

    async def test_ipv6_loopback_literal_is_not_global(self) -> None:
        fetcher = _fetcher(transport=_transport(respond=httpx.Response(200)))
        with pytest.raises(ImageFetchNotGlobalAddress):
            await fetcher.fetch("https://[::1]/a.png")

    async def test_userinfo_is_rejected(self) -> None:
        fetcher = _fetcher(transport=_transport(respond=httpx.Response(200)))
        with pytest.raises(ImageFetchInvalidUrl):
            await fetcher.fetch("https://user:pass@example.test/a.png")

    async def test_non_443_port_is_rejected(self) -> None:
        fetcher = _fetcher(transport=_transport(respond=httpx.Response(200)))
        with pytest.raises(ImageFetchInvalidUrl):
            await fetcher.fetch("https://example.test:8443/a.png")

    async def test_zone_id_is_rejected(self) -> None:
        fetcher = _fetcher(transport=_transport(respond=httpx.Response(200)))
        with pytest.raises(ImageFetchZoneId):
            await fetcher.fetch("https://[fe80::1%25eth0]/a.png")

    async def test_redirect_to_loopback_is_rejected(self) -> None:
        responses = iter(
            [
                httpx.Response(302, headers={"Location": "https://127.0.0.1/secret"}),
            ]
        )
        fetcher = _fetcher(
            resolved=[PUBLIC_V4],
            transport=_transport(responders=responses),
        )
        with pytest.raises(ImageFetchNotGlobalAddress):
            await fetcher.fetch("https://example.test/a.png")

    async def test_relative_redirect_revalidates_against_logical_url(self) -> None:
        seen: list[httpx.Request] = []
        resolve_calls: list[str] = []
        first_stream = RecordingByteStream([b"x" * 100])
        responses = iter(
            [
                httpx.Response(
                    302,
                    headers={"Location": "/b.png"},
                    stream=first_stream,
                ),
                httpx.Response(
                    200,
                    headers={"Content-Type": "image/png"},
                    content=TINY_PNG,
                ),
            ]
        )
        fetcher = _fetcher(
            resolved=[PUBLIC_V4],
            resolve_calls=resolve_calls,
            transport=_transport(on_request=seen, responders=responses),
        )

        body = await fetcher.fetch("https://example.test/a.png")

        assert body == TINY_PNG
        assert resolve_calls == ["example.test", "example.test"]
        assert seen[1].url.path == "/b.png"
        assert first_stream.iterated is False
        assert len(RecordingClient.constructed) == 2
        assert all(client.is_closed for client in RecordingClient.constructed)

    async def test_content_length_over_cap_rejects_before_body_iteration(self) -> None:
        stream = RecordingByteStream([b"x" * 100])
        fetcher = _fetcher(
            resolved=[PUBLIC_V4],
            transport=_transport(
                respond=httpx.Response(
                    200,
                    headers={
                        "Content-Type": "image/png",
                        "Content-Length": str(MAX_BODY + 1),
                    },
                    stream=stream,
                )
            ),
        )
        with pytest.raises(ImageFetchTooLarge):
            await fetcher.fetch("https://example.test/a.png")
        assert stream.iterated is False
        assert stream.chunks_yielded == 0

    async def test_streamed_overflow_without_content_length_is_rejected(self) -> None:
        overflow = RecordingByteStream([b"a" * 65_536, b"b" * 65_536, PNG_MAGIC + b"c" * MAX_BODY])
        fetcher = _fetcher(
            resolved=[PUBLIC_V4],
            transport=_transport(
                respond=httpx.Response(
                    200,
                    headers={"Content-Type": "image/png"},
                    stream=overflow,
                )
            ),
        )
        with pytest.raises(ImageFetchTooLarge):
            await fetcher.fetch("https://example.test/a.png")
        assert overflow.chunks_yielded == 3
        assert overflow.chunks_yielded < 4

    async def test_exact_cap_is_allowed_by_size_layer(self) -> None:
        body = PNG_MAGIC + b"\x00" * (MAX_BODY - len(PNG_MAGIC))
        fetcher = _fetcher(
            resolved=[PUBLIC_V4],
            transport=_transport(
                respond=httpx.Response(
                    200,
                    headers={"Content-Type": "image/png", "Content-Length": str(MAX_BODY)},
                    content=body,
                )
            ),
        )
        fetched = await fetcher.fetch("https://example.test/a.png")
        assert len(fetched) == MAX_BODY

    async def test_one_byte_over_cap_is_rejected(self) -> None:
        body = PNG_MAGIC + b"\x00" * (MAX_BODY + 1 - len(PNG_MAGIC))
        fetcher = _fetcher(
            resolved=[PUBLIC_V4],
            transport=_transport(
                respond=httpx.Response(
                    200,
                    headers={"Content-Type": "image/png"},
                    content=body,
                )
            ),
        )
        with pytest.raises(ImageFetchTooLarge):
            await fetcher.fetch("https://example.test/a.png")

    async def test_html_content_type_is_rejected_before_body(self) -> None:
        stream = RecordingByteStream([b"<html></html>"])
        fetcher = _fetcher(
            resolved=[PUBLIC_V4],
            transport=_transport(
                respond=httpx.Response(
                    200,
                    headers={"Content-Type": "text/html"},
                    stream=stream,
                )
            ),
        )
        with pytest.raises(ImageFetchBadContentType):
            await fetcher.fetch("https://example.test/a.png")
        assert stream.iterated is False

    async def test_png_type_with_gif_magic_is_rejected(self) -> None:
        fetcher = _fetcher(
            resolved=[PUBLIC_V4],
            transport=_transport(
                respond=httpx.Response(
                    200,
                    headers={"Content-Type": "image/png"},
                    content=b"GIF89a" + b"\x00" * 16,
                )
            ),
        )
        with pytest.raises(ImageFetchBadMagic):
            await fetcher.fetch("https://example.test/a.png")

    async def test_timeout_is_mapped(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("slow", request=request)

        fetcher = _fetcher(resolved=[PUBLIC_V4], transport=httpx.MockTransport(handler))
        with pytest.raises(ImageFetchTimeout):
            await fetcher.fetch("https://example.test/a.png")

    async def test_connect_error_is_mapped_to_http_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused", request=request)

        fetcher = _fetcher(resolved=[PUBLIC_V4], transport=httpx.MockTransport(handler))
        with pytest.raises(ImageFetchHttpError):
            await fetcher.fetch("https://example.test/a.png")

    async def test_dns_failure_is_mapped(self) -> None:
        def resolver(_hostname: str) -> Sequence[str]:
            raise OSError("name not found")

        fetcher = ImageFetcher(
            resolver=resolver,
            transport=_transport(respond=httpx.Response(200)),
            client_factory=_client_factory,
        )
        with pytest.raises(ImageFetchDnsFailure):
            await fetcher.fetch("https://example.test/a.png")

    async def test_http_error_status_is_mapped(self) -> None:
        fetcher = _fetcher(
            resolved=[PUBLIC_V4],
            transport=_transport(respond=httpx.Response(404, content=b"missing")),
        )
        with pytest.raises(ImageFetchHttpError):
            await fetcher.fetch("https://example.test/a.png")

    async def test_fourth_redirect_is_rejected(self) -> None:
        responses = iter(
            [
                httpx.Response(302, headers={"Location": "/1.png"}),
                httpx.Response(302, headers={"Location": "/2.png"}),
                httpx.Response(302, headers={"Location": "/3.png"}),
                httpx.Response(302, headers={"Location": "/4.png"}),
            ]
        )
        fetcher = _fetcher(
            resolved=[PUBLIC_V4],
            transport=_transport(responders=responses),
        )
        with pytest.raises(ImageFetchTooManyRedirects):
            await fetcher.fetch("https://example.test/a.png")

    async def test_client_closes_after_fetch_error(self) -> None:
        fetcher = _fetcher(
            resolved=[PUBLIC_V4],
            transport=_transport(
                respond=httpx.Response(200, headers={"Content-Type": "text/plain"}, content=b"x")
            ),
        )
        with pytest.raises(ImageFetchBadContentType):
            await fetcher.fetch("https://example.test/a.png")
        assert RecordingClient.constructed
        assert all(client.is_closed for client in RecordingClient.constructed)
