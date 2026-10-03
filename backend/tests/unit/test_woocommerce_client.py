"""Track E7 W1 — the WooCommerce client's outbound safety and error mapping.

The site address comes from a customer, so these are SSRF tests first. The
network is an ``httpx.MockTransport`` and DNS a fake resolver.
"""

from __future__ import annotations

import base64
from collections.abc import Callable, Sequence

import httpx
import pytest

from app.integrations.woocommerce import client as woo
from app.integrations.woocommerce.client import (
    WooCommerceAuthError,
    WooCommerceClient,
    WooCommerceUnreachableError,
    WooCommerceUrlRejectedError,
    normalise_site_url,
)

pytestmark = pytest.mark.unit

PUBLIC_IP = "93.184.216.34"
KEY = "ck_" + "a" * 40
SECRET = "cs_" + "b" * 40


def install(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx.Request], httpx.Response],
    addresses: Sequence[str] = (PUBLIC_IP,),
) -> list[httpx.Request]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    monkeypatch.setattr(woo, "_resolver", lambda _host: list(addresses))
    monkeypatch.setattr(woo, "_transport", httpx.MockTransport(record))
    return seen


def client(site: str = "https://shop.example.com") -> WooCommerceClient:
    return WooCommerceClient(site_url=site, consumer_key=KEY, consumer_secret=SECRET)


@pytest.mark.parametrize(
    "raw",
    [
        "http://shop.example.com",
        "https://user:pw@shop.example.com",
        "https://shop.example.com:8443",
        "https://shop.example.com/?a=1",
        "ftp://shop.example.com",
        "shop.example.com",
    ],
)
def test_only_plain_https_site_addresses_are_accepted(raw: str) -> None:
    with pytest.raises(WooCommerceUrlRejectedError):
        normalise_site_url(raw)


def test_a_site_address_is_normalised() -> None:
    assert (
        normalise_site_url(" HTTPS://Shop.Example.com/store/ ") == "https://shop.example.com/store"
    )


@pytest.mark.parametrize(
    "address", ["127.0.0.1", "10.0.0.5", "169.254.169.254", "::1", "192.168.1.1", "100.64.0.1"]
)
async def test_a_site_resolving_to_a_private_address_is_never_contacted(
    monkeypatch: pytest.MonkeyPatch, address: str
) -> None:
    seen = install(monkeypatch, lambda _r: httpx.Response(200, json=[]), addresses=[address])
    with pytest.raises(WooCommerceUrlRejectedError):
        await client().get("/settings/general")
    assert seen == []


async def test_requests_are_pinned_authenticated_and_carry_the_real_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = install(monkeypatch, lambda _r: httpx.Response(200, json=[{"id": "x"}]))
    assert await client("https://shop.example.com/store").get("/settings/general") == [{"id": "x"}]
    request = seen[0]
    assert request.url.host == PUBLIC_IP
    assert request.url.path == "/store/wp-json/wc/v3/settings/general"
    assert request.headers["host"] == "shop.example.com"
    expected = base64.b64encode(f"{KEY}:{SECRET}".encode()).decode()
    assert request.headers["authorization"] == f"Basic {expected}"


async def test_a_redirect_is_refused_not_followed(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = install(
        monkeypatch,
        lambda _r: httpx.Response(301, headers={"location": "http://169.254.169.254/"}),
    )
    with pytest.raises(WooCommerceUrlRejectedError):
        await client().get("/settings/general")
    assert len(seen) == 1


@pytest.mark.parametrize("status", [401, 403])
async def test_rejected_keys_are_reported_without_echoing_them(
    monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    install(monkeypatch, lambda _r: httpx.Response(status, json={"code": "woocommerce_rest"}))
    with pytest.raises(WooCommerceAuthError) as caught:
        await client().get("/settings/general")
    assert KEY not in str(caught.value) and SECRET not in str(caught.value.details)


async def test_a_site_without_the_rest_api_is_unreachable_not_a_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install(monkeypatch, lambda _r: httpx.Response(404, text="<html>not found</html>"))
    with pytest.raises(WooCommerceUnreachableError):
        await client().get("/settings/general")


async def test_an_oversized_response_is_cut_off(monkeypatch: pytest.MonkeyPatch) -> None:
    install(monkeypatch, lambda _r: httpx.Response(200, content=b"[" + b" " * (woo.MAX_BODY + 1)))
    with pytest.raises(woo.WooCommerceError):
        await client().get("/settings/general")
