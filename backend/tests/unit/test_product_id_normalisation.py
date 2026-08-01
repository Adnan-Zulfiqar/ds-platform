"""Accepting a listing URL where a product id is expected.

A regression guard for a live failure: a full AliExpress URL was pasted into
the import field, forwarded verbatim, and the gateway answered

    MissingParameter: The input parameter "product_id" ... is not supplied

which describes the value as *absent* rather than *malformed*. The same bare id
extracted from that URL imports successfully, verified against the live gateway.
"""

from __future__ import annotations

import pytest

from app.integrations.aliexpress.catalog import normalise_product_id
from app.schemas.product import ProductImportRequest

pytestmark = pytest.mark.unit

#: The id from the URL that failed live, and the URL it came from.
REAL_ID = "1005009558589813"
REAL_URL = f"https://www.aliexpress.com/item/{REAL_ID}.html"


class TestNormalisation:
    def test_a_bare_id_passes_through(self) -> None:
        assert normalise_product_id(REAL_ID) == REAL_ID

    def test_the_url_that_failed_live_is_reduced_to_its_id(self) -> None:
        assert normalise_product_id(REAL_URL) == REAL_ID

    @pytest.mark.parametrize(
        "url",
        [
            f"https://www.aliexpress.com/item/{REAL_ID}.html",
            f"http://www.aliexpress.com/item/{REAL_ID}.html",
            f"https://aliexpress.com/item/{REAL_ID}.html",
            f"https://www.aliexpress.us/item/{REAL_ID}.html",
            f"https://es.aliexpress.com/item/{REAL_ID}.html",
            f"https://www.aliexpress.com/item/{REAL_ID}.html?spm=a2g0o.detail",
            f"www.aliexpress.com/item/{REAL_ID}.html",
            f"/item/{REAL_ID}.html",
        ],
    )
    def test_url_variants_all_yield_the_id(self, url: str) -> None:
        """Regional hosts, locale prefixes and tracking parameters all appear in
        real pasted URLs. Only the `/item/<digits>` segment is stable."""
        assert normalise_product_id(url) == REAL_ID

    def test_surrounding_whitespace_is_ignored(self) -> None:
        assert normalise_product_id(f"  {REAL_ID}  ") == REAL_ID

    def test_a_decorated_id_is_recovered_when_unambiguous(self) -> None:
        assert normalise_product_id(f'"{REAL_ID}"') == REAL_ID

    @pytest.mark.parametrize("value", ["", "   ", "not-a-product", "abc123def"])
    def test_unrecoverable_values_return_none(self, value: str) -> None:
        """None rather than a guess. Forwarding a guess to the supplier trades a
        clear local error for a misleading remote one."""
        assert normalise_product_id(value) is None

    def test_an_ambiguous_string_is_refused(self) -> None:
        """Two candidate ids and no way to choose.

        Picking the first would be a coin flip that imports the wrong product,
        which is worse than asking.
        """
        assert normalise_product_id("1005009558589813 3256806389000685") is None


class TestImportRequestValidation:
    """The normalisation runs at the request boundary, so every caller gets it."""

    def test_a_pasted_url_is_accepted_and_reduced(self) -> None:
        request = ProductImportRequest.model_validate({"externalId": REAL_URL})
        assert request.external_id == REAL_ID

    def test_a_bare_id_is_accepted(self) -> None:
        request = ProductImportRequest.model_validate({"externalId": REAL_ID})
        assert request.external_id == REAL_ID

    def test_an_unusable_value_is_rejected_with_a_useful_message(self) -> None:
        """The message names the real problem.

        The supplier's own error calls a malformed id "not supplied", which is
        what made this cost an afternoon rather than a minute.
        """
        with pytest.raises(ValueError, match="AliExpress product ID"):
            ProductImportRequest.model_validate({"externalId": "not-a-product"})

    def test_a_url_longer_than_the_old_limit_is_accepted(self) -> None:
        """The field used to cap at 128 characters — shorter than many real
        listing URLs once tracking parameters are attached, so a paste was
        rejected before normalisation could rescue it."""
        long_url = f"https://www.aliexpress.com/item/{REAL_ID}.html?spm={'a' * 200}"
        request = ProductImportRequest.model_validate({"externalId": long_url})
        assert request.external_id == REAL_ID
