"""EBAY-C6 — readiness rule, call-limit parsing, 429 mapping. No network."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from app.core.config import EbayEnvironment
from app.core.production_readiness import Status, _check_ebay_seller_channel
from app.integrations.ebay.analytics import parse_rate_limits
from app.integrations.ebay.exceptions import EbayRateLimitedError, EbaySellerApiUnavailableError
from app.integrations.ebay.seller_setup import EbaySellerClient

pytestmark = pytest.mark.unit


def ebay_settings(**overrides: Any) -> Any:
    values: dict[str, Any] = {
        "client_id": "DropPilo-TestOnly-PRD-0",
        "client_secret": SecretStr("test-only"),
        "redirect_uri_name": "DropPilot-TestOnly-RuName",
        "marketplace_deletion_verification_token": SecretStr("t" * 40),
        "marketplace_deletion_endpoint": "https://api.example.invalid/ebay/deletion",
        "environment": EbayEnvironment.PRODUCTION,
    }
    values.update(overrides)
    return SimpleNamespace(ebay=SimpleNamespace(**values))


class TestReadinessRule:
    def test_off_is_skipped_not_a_defect(self) -> None:
        assert _check_ebay_seller_channel(ebay_settings(client_id="")).status is Status.SKIPPED

    def test_fully_configured_passes_and_names_the_environment(self) -> None:
        finding = _check_ebay_seller_channel(ebay_settings())
        assert finding.status is Status.PASS
        assert "production" in finding.reason

    @pytest.mark.parametrize(
        ("override", "named"),
        [
            ({"client_secret": None}, "EBAY_CLIENT_SECRET"),
            ({"redirect_uri_name": " "}, "EBAY_REDIRECT_URI_NAME"),
            (
                {"marketplace_deletion_verification_token": None},
                "EBAY_MARKETPLACE_DELETION_VERIFICATION_TOKEN",
            ),
            ({"marketplace_deletion_endpoint": ""}, "EBAY_MARKETPLACE_DELETION_ENDPOINT"),
        ],
    )
    def test_half_configured_is_missing_and_names_what(
        self, override: dict[str, Any], named: str
    ) -> None:
        finding = _check_ebay_seller_channel(ebay_settings(**override))
        assert finding.status is Status.MISSING
        assert named in finding.reason
        assert "test-only" not in finding.reason  # never a value


def test_rate_limits_keep_only_the_sell_apis_we_call() -> None:
    parsed = parse_rate_limits(
        {
            "rateLimits": [
                {
                    "apiName": "inventory",
                    "resources": [
                        {
                            "name": "sell.inventory",
                            "rates": [{"limit": 2000000, "remaining": 1999000, "reset": "x"}],
                        }
                    ],
                },
                {
                    "apiName": "marketing",
                    "resources": [{"name": "m", "rates": [{"limit": 1, "remaining": 1}]}],
                },
                {
                    "apiName": "fulfillment",
                    "resources": [{"name": "f", "rates": [{"limit": "n/a"}]}],
                },
            ]
        }
    )
    assert [(c.api, c.limit, c.remaining) for c in parsed] == [("inventory", 2000000, 1999000)]
    assert parse_rate_limits("not a body") == ()


@pytest.mark.parametrize(
    ("status", "error"),
    [(429, EbayRateLimitedError), (503, EbaySellerApiUnavailableError)],
)
def test_429_is_a_rate_limit_not_an_outage(status: int, error: type[Exception]) -> None:
    with pytest.raises(error):
        EbaySellerClient._raise_for(httpx.Response(status), call="test")


def test_capitalised_api_names_are_recognised() -> None:
    parsed = parse_rate_limits(
        {
            "rateLimits": [
                {
                    "apiName": "Inventory",
                    "resources": [{"name": "r", "rates": [{"limit": 5, "remaining": 4}]}],
                }
            ]
        }
    )
    assert [(c.api, c.remaining) for c in parsed] == [("inventory", 4)]
