"""The title each channel receives (owner request 2026-10-12)."""

from __future__ import annotations

import pytest

from app.domain.listing_title import channel_title, clean_title

pytestmark = pytest.mark.unit

#: The owner's own draft, as imported from AliExpress.
MAGSAFE = (
    "Luxury For Magsafe Transparent Case For iPhone 18 17 16 15 14 13 12 11 "
    "Pro Max Mini XR Magnetic Wireless Charge Back Cover Case"
)


def test_the_owners_title_fits_ebay_at_a_whole_word() -> None:
    title = channel_title(MAGSAFE, "ebay")
    assert len(title) <= 80
    assert MAGSAFE.startswith(title)  # nothing invented, nothing reordered
    assert not title.endswith((" For", " for", "-", ","))


def test_the_owners_title_is_unchanged_where_it_fits() -> None:
    assert channel_title(MAGSAFE, "shopify") == MAGSAFE
    assert channel_title(MAGSAFE, "woocommerce") == MAGSAFE


@pytest.mark.parametrize(
    ("raw", "cleaned"),
    [
        ("Desk Lamp Free Shipping", "Desk Lamp"),
        ("Hot Sale 2026 Desk Lamp (Free Shipping)", "2026 Desk Lamp"),
        ("Desk Lamp - Dropshipping - LED", "Desk Lamp - LED"),
        ("  Desk   Lamp ,  LED  ", "Desk Lamp, LED"),
        ("Drop Shipping Phone Case", "Phone Case"),
        ("Wholesale New Phone Case", "Wholesale New Phone Case"),  # real words stay
    ],
)
def test_seller_filler_is_removed_on_every_channel(raw: str, cleaned: str) -> None:
    assert clean_title(raw) == cleaned
    for channel in ("ebay", "shopify", "woocommerce"):
        assert channel_title(raw, channel) == cleaned


def test_repeated_words_go_before_anything_is_cut() -> None:
    raw = (
        "Phone Case Silicone Phone Case Soft Phone Case Cover for iPhone 15 Pro "
        "Shockproof Case Cover"
    )
    title = channel_title(raw, "ebay")
    assert title == "Phone Case Silicone Soft Cover for iPhone 15 Pro Shockproof"
    assert len(title) <= 80


def test_model_numbers_may_repeat() -> None:
    raw = "Case for iPhone 15 Pro and iPhone 15 Plus " + "Magnetic Clear Back Shell " * 3
    title = channel_title(raw, "ebay")
    # The repeated word goes; the repeated model number stays.
    assert title.startswith("Case for iPhone 15 Pro and 15 Plus")


def test_a_title_within_the_limit_keeps_its_repeats() -> None:
    raw = "Phone Case Phone Case"
    assert channel_title(raw, "ebay") == raw


def test_a_single_overlong_word_is_cut_hard() -> None:
    assert channel_title("x" * 120, "ebay") == "x" * 80


def test_shopify_is_capped_at_255() -> None:
    raw = " ".join(f"word{i}" for i in range(80))
    title = channel_title(raw, "shopify")
    assert len(title) <= 255 and raw.startswith(title)


def test_an_empty_title_stays_empty() -> None:
    assert channel_title("", "ebay") == ""
    assert channel_title("Free Shipping", "ebay") == ""
