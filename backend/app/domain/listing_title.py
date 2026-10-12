"""The title each sales channel receives, derived from the draft's title.

Rule-based, not AI (owner decision 2026-10-12): it runs at every publish,
offline, and gives the same answer every time, so publish readiness can
show the merchant the exact title before anything is sent.

Two steps:

1. **Clean, for every channel.** AliExpress titles carry seller filler --
   "Free Shipping", "Hot Sale", "Dropshipping" -- that is noise on a shop
   front and, on eBay, can breach listing policy. Whitespace and dangling
   separators are tidied.
2. **Fit the channel's limit.** eBay allows 80 characters, Shopify 255;
   WooCommerce has no practical limit. A title over the limit first loses
   repeated words (AliExpress repeats "Case", "Phone", "Cover"), then is
   cut at a whole word and never left ending on a connector ("for",
   "with", "-"). A title already within the limit keeps its words: removing
   repeats when nothing needs to fit would rewrite text the merchant chose.

An approved AI version's title is not passed through here: it was reviewed
as written (Stage 7) and is sent verbatim.
"""

from __future__ import annotations

import re

#: Character limits, by channel. ``None``: no limit worth enforcing.
TITLE_LIMITS: dict[str, int | None] = {
    "ebay": 80,
    "shopify": 255,
    "woocommerce": None,
}

#: Seller filler removed from every channel's title, matched as whole
#: phrases, case-insensitively. Kept short and unambiguous on purpose: a
#: word that could be part of a real product name ("wholesale", "new") is
#: not on it.
_FILLER = re.compile(
    r"\b(?:free\s+shipping|hot\s+sale|hot\s+selling|drop\s*shipping|dropship)\b",
    re.IGNORECASE,
)
#: Brackets left empty once filler is removed, e.g. "Lamp (Free Shipping)".
_EMPTY_BRACKETS = re.compile(r"[(\[{]\s*[)\]}]")
#: A separator with nothing on one side of it, e.g. "Lamp , Desk" or "- Lamp".
_LOOSE_COMMA = re.compile(r"\s+,")
_REPEATED_SEPARATORS = re.compile(r"(?:\s*[-|/,]\s*){2,}")

#: Words a title may repeat without being stuffed, and must not end on.
_CONNECTORS = frozenset({"for", "with", "and", "or", "the", "a", "an", "of", "to", "in", "on"})
_TRAILING_JUNK = "-|/,+&:;"


def clean_title(title: str) -> str:
    """The title without seller filler, with tidy spacing and separators."""
    text = _FILLER.sub(" ", title or "")
    text = _EMPTY_BRACKETS.sub(" ", text)
    text = _REPEATED_SEPARATORS.sub(" - ", text)
    text = " ".join(text.split())
    text = _LOOSE_COMMA.sub(",", text)
    return _strip_edges(text)


def channel_title(title: str, channel: str) -> str:
    """The title ``channel`` is sent for a draft titled ``title``."""
    text = clean_title(title)
    limit = TITLE_LIMITS.get(channel)
    if limit is None or len(text) <= limit:
        return text
    text = _without_repeated_words(text)
    if len(text) <= limit:
        return text
    return _cut_at_word(text, limit)


def _without_repeated_words(text: str) -> str:
    """Drop a word's later repeats; connectors and numbers may repeat.

    Numbers stay because "iPhone 15 ... 15 Pro" style model lists need them,
    and connectors because "Case for iPhone for Women" reads wrong without.
    """
    seen: set[str] = set()
    kept: list[str] = []
    for word in text.split(" "):
        key = word.strip(_TRAILING_JUNK + ".!?()[]").lower()
        if key and key.isalpha() and key not in _CONNECTORS and len(key) > 2:
            if key in seen:
                continue
            seen.add(key)
        kept.append(word)
    return _strip_edges(" ".join(kept))


def _cut_at_word(text: str, limit: int) -> str:
    cut = text[: limit + 1]
    if len(text) > limit and " " in cut:
        cut = cut[: cut.rfind(" ")]
    cut = cut[:limit]
    words = cut.split(" ")
    while len(words) > 1 and (
        words[-1].lower() in _CONNECTORS or not words[-1].strip(_TRAILING_JUNK)
    ):
        words.pop()
    return _strip_edges(" ".join(words))


def _strip_edges(text: str) -> str:
    return text.strip().strip(_TRAILING_JUNK).strip()


__all__ = ["TITLE_LIMITS", "channel_title", "clean_title"]
