"""String utilities."""

from __future__ import annotations

import re
import unicodedata

_NON_ALPHANUMERIC = re.compile(r"[^a-z0-9]+")
_REPEATED_HYPHENS = re.compile(r"-{2,}")


def slugify(value: str, *, max_length: int = 63) -> str:
    """Convert arbitrary text into a DNS-safe label.

    Used for tenant subdomains, so the output must satisfy the ``CHECK``
    constraint on ``tenants.slug``: lowercase alphanumerics and internal
    hyphens only, no leading or trailing hyphen.

    Unicode is transliterated rather than stripped, so "Café Münster" yields
    ``cafe-munster`` instead of ``caf-nster``. NFKD decomposition splits accented
    characters into a base letter plus a combining mark; dropping the marks
    leaves the readable ASCII skeleton.

    Returns an empty string when nothing usable survives — callers must handle
    that rather than assume a valid slug, since input like "***" legitimately
    produces nothing.

    >>> slugify("Café Münster GmbH")
    'cafe-munster-gmbh'
    >>> slugify("  --Hello, World!--  ")
    'hello-world'
    >>> slugify("***")
    ''
    """
    if not value:
        return ""

    decomposed = unicodedata.normalize("NFKD", value)
    ascii_only = decomposed.encode("ascii", "ignore").decode("ascii")

    slug = _NON_ALPHANUMERIC.sub("-", ascii_only.lower())
    slug = _REPEATED_HYPHENS.sub("-", slug).strip("-")

    if len(slug) > max_length:
        # Trim at the boundary rather than mid-word where possible, then strip
        # any hyphen the cut left dangling.
        slug = slug[:max_length].rstrip("-")

    return slug


def truncate(value: str, max_length: int, *, suffix: str = "...") -> str:
    """Shorten a string to ``max_length`` including the suffix.

    >>> truncate("abcdefghij", 8)
    'abcde...'
    >>> truncate("short", 10)
    'short'
    """
    if len(value) <= max_length:
        return value
    if max_length <= len(suffix):
        return value[:max_length]
    return value[: max_length - len(suffix)] + suffix


__all__ = ["slugify", "truncate"]
