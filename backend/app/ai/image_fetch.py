"""SSRF-safe HTTPS fetch for `ProductImage` URLs.

Logical URL and connection URL are different values. DNS is resolved once
through an injected resolver, every address is judged with `is_global`, and
httpx is told to open a TLS connection to an already-validated IP literal
while sending the original hostname as `Host` and SNI. That is what stops
httpx from doing a second lookup — including to loopback or CGNAT — and
what stops `trust_env` proxies from rewriting the hop.

Redirects re-run the whole contract against the logical URL, never the IP
connection URL. The body is streamed and aborted at 5 MiB; `read()` /
`aread()` are not used.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Callable, Sequence
from typing import Final
from urllib.parse import urljoin, urlparse, urlunparse

import httpx

from app.core.exceptions import AppError

MAX_BODY: Final[int] = 5_242_880
CONNECT_TIMEOUT_SECONDS: Final[float] = 3.0
READ_TIMEOUT_SECONDS: Final[float] = 10.0
#: Whole-fetch budget across DNS, every redirect hop and the body (review
#: finding B-3). The per-read timeout alone let a host that trickles one
#: chunk every few seconds hold a fetch — and the bulk run's row lock —
#: until the 5 MiB cap.
TOTAL_DEADLINE_SECONDS: Final[float] = 30.0
#: RFC 6052 well-known NAT64 prefix. Python reports it as globally
#: reachable whatever IPv4 address it embeds, so ``64:ff9b::a9fe:a9fe`` (the
#: metadata service, through a NAT64 gateway) would pass ``is_global``.
NAT64_WELL_KNOWN: Final = ipaddress.IPv6Network("64:ff9b::/96")
#: Deprecated IPv4-compatible addresses (RFC 4291 §2.5.5.1). ``::127.0.0.1``
#: is also reported as global; nothing legitimate serves images from one.
IPV4_COMPATIBLE: Final = ipaddress.IPv6Network("::/96")
MAX_REDIRECTS: Final[int] = 3
REDIRECT_STATUSES: Final[frozenset[int]] = frozenset({301, 302, 303, 307, 308})
ALLOWED_MEDIA_TYPES: Final[frozenset[str]] = frozenset({"image/jpeg", "image/jpg", "image/png"})
BLOCKED_HOSTNAMES: Final[frozenset[str]] = frozenset(
    {
        "localhost",
        "localhost.",
        "metadata.google.internal",
        "metadata.internal",
    }
)
JPEG_MAGIC: Final[bytes] = b"\xff\xd8\xff"
PNG_MAGIC: Final[bytes] = b"\x89PNG\r\n\x1a\n"

HostnameResolver = Callable[[str], Sequence[str]]
ClientFactory = Callable[..., httpx.AsyncClient]


class ImageFetchError(AppError):
    """Expected per-image fetch/decode failure. Not a product-level abort."""

    code = "image_fetch_error"
    status_code = 422
    message = "The image could not be fetched."


class ImageFetchDisallowedScheme(ImageFetchError):
    code = "image_fetch_disallowed_scheme"
    message = "Only https image URLs are allowed."


class ImageFetchInvalidUrl(ImageFetchError):
    code = "image_fetch_invalid_url"
    message = "The image URL is not fetchable."


class ImageFetchNotGlobalAddress(ImageFetchError):
    code = "image_fetch_not_global_address"
    message = "The image host does not resolve to a globally reachable address."


class ImageFetchHostnameBlocked(ImageFetchError):
    code = "image_fetch_hostname_blocked"
    message = "The image hostname is not allowed."


class ImageFetchZoneId(ImageFetchError):
    code = "image_fetch_zone_id"
    message = "IPv6 zone identifiers are not allowed."


class ImageFetchDnsFailure(ImageFetchError):
    code = "image_fetch_dns_failure"
    message = "The image hostname could not be resolved."


class ImageFetchTimeout(ImageFetchError):
    code = "image_fetch_timeout"
    message = "The image fetch timed out."


class ImageFetchHttpError(ImageFetchError):
    code = "image_fetch_http_error"
    message = "The image host returned an unexpected HTTP status."


class ImageFetchTooLarge(ImageFetchError):
    code = "image_fetch_too_large"
    message = "The image exceeded the maximum download size."


class ImageFetchBadContentType(ImageFetchError):
    code = "image_fetch_bad_content_type"
    message = "The image Content-Type is not JPEG or PNG."


class ImageFetchBadMagic(ImageFetchError):
    code = "image_fetch_bad_magic"
    message = "The image bytes are not JPEG or PNG."


class ImageFetchTooManyRedirects(ImageFetchError):
    code = "image_fetch_too_many_redirects"
    message = "The image fetch followed too many redirects."


class ImageFetchPixelLimit(ImageFetchError):
    code = "image_fetch_pixel_limit"
    message = "The image exceeds the decoded pixel limit."


class ImageFetchDecodeFailed(ImageFetchError):
    code = "image_fetch_decode_failed"
    message = "The image could not be decoded as JPEG or PNG."


def system_resolve(hostname: str) -> Sequence[str]:
    """One `getaddrinfo` lookup, preserving resolver order."""
    try:
        infos = socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ImageFetchDnsFailure from exc
    addresses: list[str] = []
    seen: set[str] = set()
    for info in infos:
        host = info[4][0]
        if not isinstance(host, str):
            continue
        if host not in seen:
            seen.add(host)
            addresses.append(host)
    if not addresses:
        raise ImageFetchDnsFailure
    return addresses


def normalize_ip(raw: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    """Unwrap IPv4-mapped IPv6 and reject scoped addresses."""
    try:
        addr: ipaddress.IPv4Address | ipaddress.IPv6Address = ipaddress.ip_address(raw)
    except ValueError as exc:
        raise ImageFetchNotGlobalAddress from exc
    if isinstance(addr, ipaddress.IPv6Address):
        if addr.scope_id:
            raise ImageFetchZoneId
        mapped = addr.ipv4_mapped
        if mapped is not None:
            addr = mapped
    return addr


def _reachable_target(
    addr: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    """The address whose reachability actually decides the hop.

    A NAT64 address is judged by the IPv4 address it embeds — that is where
    the packet ends up. IPv4-compatible addresses are refused outright.
    """
    if isinstance(addr, ipaddress.IPv6Address):
        if addr in NAT64_WELL_KNOWN:
            return ipaddress.IPv4Address(int(addr) & 0xFFFF_FFFF)
        if addr in IPV4_COMPATIBLE:
            raise ImageFetchNotGlobalAddress
    return addr


def choose_connectable(raw_addrs: Sequence[str]) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    """Allow a hop only when every resolved address is globally reachable.

    The connection still targets the address as resolved; only the judgement
    looks through NAT64 to the embedded IPv4 address.
    """
    if not raw_addrs:
        raise ImageFetchDnsFailure
    normalised = [normalize_ip(raw) for raw in raw_addrs]
    if any(not _reachable_target(addr).is_global for addr in normalised):
        raise ImageFetchNotGlobalAddress
    return normalised[0]


def _idna_hostname(host: str) -> str:
    try:
        return host.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise ImageFetchInvalidUrl from exc


def _parse_logical(logical_url: str) -> tuple[str, str, str, str]:
    parsed = urlparse(logical_url)
    if parsed.scheme.lower() != "https":
        raise ImageFetchDisallowedScheme
    if parsed.username is not None or parsed.password is not None:
        raise ImageFetchInvalidUrl
    if parsed.port is not None and parsed.port != 443:
        raise ImageFetchInvalidUrl
    host = parsed.hostname
    if host is None or host == "":
        raise ImageFetchInvalidUrl
    if "%" in host:
        raise ImageFetchZoneId
    try:
        ipaddress.ip_address(host)
        logical_hostname = host.lower()
        is_literal = True
    except ValueError:
        logical_hostname = _idna_hostname(host)
        is_literal = False
    if logical_hostname in BLOCKED_HOSTNAMES:
        raise ImageFetchHostnameBlocked
    path = parsed.path if parsed.path else "/"
    return logical_hostname, path, parsed.query, "literal" if is_literal else "name"


def _connection_url(
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address,
    *,
    path: str,
    query: str,
) -> str:
    netloc = f"[{ip}]" if isinstance(ip, ipaddress.IPv6Address) else str(ip)
    return urlunparse(("https", netloc, path, "", query, ""))


async def pin_https_target(
    logical_url: str, *, resolver: HostnameResolver = system_resolve
) -> tuple[str, str]:
    """Validate one HTTPS hop and pin it to a vetted address.

    Returns ``(logical_hostname, connection_url)``. The caller connects to
    ``connection_url`` and sends ``logical_hostname`` as ``Host`` and SNI, so
    httpx never resolves the name a second time. Shared by every outbound
    call to a URL a customer supplied (images; WooCommerce sites), so there
    is exactly one SSRF contract. Raises an :class:`ImageFetchError`
    subclass when the URL or any resolved address is not acceptable.
    """
    logical_hostname, path, query, kind = _parse_logical(logical_url)
    if kind == "literal":
        chosen = choose_connectable([logical_hostname])
    else:
        try:
            # `getaddrinfo` blocks; run it off the event loop so one slow
            # resolver cannot stall every other request (finding B-3).
            resolved = await asyncio.to_thread(resolver, logical_hostname)
        except ImageFetchError:
            raise
        except OSError as exc:
            raise ImageFetchDnsFailure from exc
        chosen = choose_connectable(resolved)
    return logical_hostname, _connection_url(chosen, path=path, query=query)


def _media_type(content_type: str | None) -> str:
    return (content_type or "").split(";", 1)[0].strip().lower()


def _declared_length(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


class ImageFetcher:
    """Fetch one HTTPS image URL under the Stage 6 SSRF contract."""

    def __init__(
        self,
        *,
        resolver: HostnameResolver | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        client_factory: ClientFactory | None = None,
    ) -> None:
        self._resolver = resolver or system_resolve
        self._transport = transport
        self._client_factory = client_factory or httpx.AsyncClient

    async def fetch(self, url: str) -> bytes:
        try:
            async with asyncio.timeout(TOTAL_DEADLINE_SECONDS):
                return await self._fetch_hops(url)
        except TimeoutError as exc:
            raise ImageFetchTimeout from exc

    async def _fetch_hops(self, url: str) -> bytes:
        logical_url = url
        redirects_seen = 0
        while True:
            kind, payload = await self._hop(logical_url)
            if kind == "redirect":
                redirects_seen += 1
                if redirects_seen > MAX_REDIRECTS:
                    raise ImageFetchTooManyRedirects
                if not isinstance(payload, str) or not payload:
                    raise ImageFetchHttpError
                logical_url = urljoin(logical_url, payload)
                continue
            if not isinstance(payload, bytes):
                raise ImageFetchHttpError
            return payload

    async def _hop(self, logical_url: str) -> tuple[str, str] | tuple[str, bytes]:
        logical_hostname, connection_url = await pin_https_target(
            logical_url, resolver=self._resolver
        )
        timeout = httpx.Timeout(READ_TIMEOUT_SECONDS, connect=CONNECT_TIMEOUT_SECONDS)
        try:
            if self._transport is not None:
                client_cm = self._client_factory(
                    timeout=timeout,
                    follow_redirects=False,
                    trust_env=False,
                    verify=True,
                    transport=self._transport,
                )
            else:
                client_cm = self._client_factory(
                    timeout=timeout,
                    follow_redirects=False,
                    trust_env=False,
                    verify=True,
                )
            async with client_cm as client:
                return await self._stream_hop(
                    client,
                    connection_url=connection_url,
                    logical_hostname=logical_hostname,
                )
        except ImageFetchError:
            raise
        except httpx.TimeoutException as exc:
            raise ImageFetchTimeout from exc
        except httpx.RequestError as exc:
            raise ImageFetchHttpError from exc

    async def _stream_hop(
        self,
        client: httpx.AsyncClient,
        *,
        connection_url: str,
        logical_hostname: str,
    ) -> tuple[str, str] | tuple[str, bytes]:
        async with client.stream(
            "GET",
            connection_url,
            headers={"Host": logical_hostname},
            extensions={"sni_hostname": logical_hostname},
        ) as response:
            if response.status_code in REDIRECT_STATUSES:
                location = response.headers.get("location")
                return ("redirect", location or "")
            if response.status_code != 200:
                raise ImageFetchHttpError
            if _media_type(response.headers.get("content-type")) not in ALLOWED_MEDIA_TYPES:
                raise ImageFetchBadContentType
            declared = _declared_length(response.headers.get("content-length"))
            if declared is not None and declared > MAX_BODY:
                raise ImageFetchTooLarge
            chunks: list[bytes] = []
            received = 0
            async for chunk in response.aiter_bytes(chunk_size=65_536):
                received += len(chunk)
                if received > MAX_BODY:
                    raise ImageFetchTooLarge
                chunks.append(chunk)
            body = b"".join(chunks)
        if not (body.startswith(JPEG_MAGIC) or body.startswith(PNG_MAGIC)):
            raise ImageFetchBadMagic
        return ("body", body)


__all__ = [
    "ALLOWED_MEDIA_TYPES",
    "BLOCKED_HOSTNAMES",
    "CONNECT_TIMEOUT_SECONDS",
    "IPV4_COMPATIBLE",
    "JPEG_MAGIC",
    "MAX_BODY",
    "MAX_REDIRECTS",
    "NAT64_WELL_KNOWN",
    "PNG_MAGIC",
    "READ_TIMEOUT_SECONDS",
    "REDIRECT_STATUSES",
    "TOTAL_DEADLINE_SECONDS",
    "ClientFactory",
    "HostnameResolver",
    "ImageFetchBadContentType",
    "ImageFetchBadMagic",
    "ImageFetchDecodeFailed",
    "ImageFetchDisallowedScheme",
    "ImageFetchDnsFailure",
    "ImageFetchError",
    "ImageFetchHostnameBlocked",
    "ImageFetchHttpError",
    "ImageFetchInvalidUrl",
    "ImageFetchNotGlobalAddress",
    "ImageFetchPixelLimit",
    "ImageFetchTimeout",
    "ImageFetchTooLarge",
    "ImageFetchTooManyRedirects",
    "ImageFetchZoneId",
    "ImageFetcher",
    "choose_connectable",
    "normalize_ip",
    "pin_https_target",
    "system_resolve",
]
