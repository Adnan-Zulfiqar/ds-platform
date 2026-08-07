"""ISO 3166-1 alpha-2 helpers for AliExpress ship-to destinations.

Country codes are the wire contract; display names are for merchant-facing
errors and the import dialog. The list is intentionally broader than the
dialog's short favourites so error copy can name uncommon destinations.
"""

from __future__ import annotations

COUNTRY_NAMES: dict[str, str] = {
    "US": "United States",
    "GB": "United Kingdom",
    "CA": "Canada",
    "AU": "Australia",
    "DE": "Germany",
    "FR": "France",
    "NL": "Netherlands",
    "IT": "Italy",
    "ES": "Spain",
    "PL": "Poland",
    "BE": "Belgium",
    "IE": "Ireland",
    "SE": "Sweden",
    "NO": "Norway",
    "DK": "Denmark",
    "FI": "Finland",
    "AT": "Austria",
    "CH": "Switzerland",
    "PT": "Portugal",
    "NZ": "New Zealand",
    "JP": "Japan",
    "KR": "South Korea",
    "SG": "Singapore",
    "HK": "Hong Kong",
    "MX": "Mexico",
    "BR": "Brazil",
    "AE": "United Arab Emirates",
    "SA": "Saudi Arabia",
    "IN": "India",
}


#: Destination -> selling-currency for markets DropPilot actively supports
#: (M24B). Deliberately short: inventing a currency for a country nobody has
#: approved as a market is a business decision, not a mapping exercise.
#: Extend this dict, in its own reviewed change, when a new market is added —
#: never fall back to guessing one.
CURRENCY_BY_COUNTRY: dict[str, str] = {
    "GB": "GBP",
    "US": "USD",
}


def currency_for_country(code: str | None) -> str | None:
    """Selling currency for a supported destination, or ``None`` if unmapped.

    ``None`` is a normal outcome for any destination outside the initial
    market list — callers fall back to tenant/workspace currency rather than
    treating it as an error.
    """
    normalised = normalise_country_code(code)
    if normalised is None:
        return None
    return CURRENCY_BY_COUNTRY.get(normalised)


def normalise_country_code(value: str | None) -> str | None:
    """Return a two-letter uppercase code, or ``None`` if unusable."""
    if value is None:
        return None
    code = value.strip().upper()
    if len(code) != 2 or not code.isalpha():
        return None
    return code


def country_display_name(code: str | None) -> str:
    """Human label for a code; falls back to the code itself."""
    normalised = normalise_country_code(code)
    if normalised is None:
        return "the selected destination"
    return COUNTRY_NAMES.get(normalised, normalised)


__all__ = [
    "COUNTRY_NAMES",
    "CURRENCY_BY_COUNTRY",
    "country_display_name",
    "currency_for_country",
    "normalise_country_code",
]
