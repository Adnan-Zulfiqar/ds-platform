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
    "country_display_name",
    "normalise_country_code",
]
