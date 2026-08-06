"""Unit tests for AliExpress ship-to destination resolution."""

from __future__ import annotations

import pytest

from app.integrations.aliexpress.countries import (
    country_display_name,
    normalise_country_code,
)
from app.services.import_destination import country_from_store_settings

pytestmark = pytest.mark.unit


class TestCountryHelpers:
    def test_normalises_alpha2(self) -> None:
        assert normalise_country_code(" gb ") == "GB"
        assert normalise_country_code("USA") is None
        assert normalise_country_code(None) is None

    def test_display_names(self) -> None:
        assert country_display_name("US") == "United States"
        assert country_display_name("GB") == "United Kingdom"


class TestStoreSettingsCountry:
    def test_reads_camel_case_country_code(self) -> None:
        assert country_from_store_settings({"countryCode": "de"}) == "DE"

    def test_ignores_blank_and_invalid(self) -> None:
        assert country_from_store_settings({"countryCode": ""}) is None
        assert country_from_store_settings({"country": "USA"}) is None
        assert country_from_store_settings(None) is None
