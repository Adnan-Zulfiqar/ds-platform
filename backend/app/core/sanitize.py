"""Sanitization for supplier-authored HTML.

Product descriptions arrive as seller-authored markup from an external
supplier (AliExpress today; any future supplier that sends HTML inherits the
same treatment, since nothing here is AliExpress-specific). It is untrusted
input, and it is sanitized exactly once, here, before it is ever written to
the database — never at render time, and never by a hand-rolled regex, which
is a well-known way to build a filter that looks like it works until the
first encoding trick defeats it.

``nh3`` (the Rust ``ammonia`` crate via PyO3) does the actual parsing and
rewriting: it is a real HTML parser operating on an explicit tag/attribute
**allowlist**, not a blocklist of known-bad patterns — which is what makes it
resistant to the case, encoding, and nesting tricks that defeat a regex-based
sanitizer.
"""

from __future__ import annotations

import html as _html_entities
import re

import nh3

#: Structural and text-formatting elements a product description can
#: reasonably need. Notably absent: `div`/`span` — nh3 unwraps a disallowed
#: tag and keeps its (still-sanitized) content by default, so nothing is lost
#: by leaving generic containers out — and `iframe`/`script`/`style`/`form`/
#: `input`/`button`/`select`/`textarea`, none of which is ever legitimate in
#: a product description.
_ALLOWED_TAGS = frozenset(
    {
        "p",
        "br",
        "strong",
        "b",
        "em",
        "i",
        "u",
        "s",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "ul",
        "ol",
        "li",
        "a",
        "img",
        "blockquote",
        "table",
        "thead",
        "tbody",
        "tfoot",
        "tr",
        "td",
        "th",
    }
)

#: Per-tag attribute allowlist. Deliberately no `style`, `class`, or `id`
#: anywhere: refusing dangerous inline styles by omission rather than trying
#: to filter individual CSS properties, and there is no stylesheet on this
#: platform for a `class` to hook into anyway. Anything not listed here for a
#: tag — including every `on*` event-handler attribute — is stripped, not
#: merely escaped.
_ALLOWED_ATTRIBUTES: dict[str, frozenset[str]] = {
    "a": frozenset({"href", "title"}),
    "img": frozenset({"src", "alt", "title"}),
    "td": frozenset({"colspan", "rowspan"}),
    "th": frozenset({"colspan", "rowspan"}),
}

#: Only these schemes survive on `href`/`src`. This is the actual defence
#: against `javascript:`, `data:`, and `vbscript:` links — a tag/attribute
#: allowlist alone does not stop a malicious *value* in an otherwise-allowed
#: attribute.
_ALLOWED_URL_SCHEMES = frozenset({"http", "https"})

#: Tags whose *content* is discarded along with the tag, not merely unwrapped.
#: A `<script>alert(1)</script>` that only had its tag stripped would leave
#: the literal text `alert(1)` behind — dropping the wrapper is not enough
#: for these.
_CLEAN_CONTENT_TAGS = frozenset({"script", "style", "noscript", "iframe", "object", "embed"})

_cleaner = nh3.Cleaner(
    tags=_ALLOWED_TAGS,
    attributes=_ALLOWED_ATTRIBUTES,
    clean_content_tags=_CLEAN_CONTENT_TAGS,
    url_schemes=_ALLOWED_URL_SCHEMES,
    link_rel="noopener noreferrer nofollow",
    strip_comments=True,
)


def sanitize_html(raw: str | None) -> str | None:
    """Reduce supplier-authored HTML to the allowlisted subset above.

    Deterministic and side-effect-free — the same input always produces the
    same output — which is what makes it safe to call on every import
    without accumulating drift between runs.

    Returns ``None`` for ``None`` or blank input rather than ``""``, so a
    product with no description stays distinguishable from one whose
    description sanitized down to nothing.
    """
    if raw is None:
        return None
    trimmed = raw.strip()
    if not trimmed:
        return None
    cleaned = _cleaner.clean(trimmed)
    return cleaned or None


_BLOCK_CLOSE_RE = re.compile(r"(?i)</\s*(p|div|li|h[1-6]|tr|blockquote)\s*>")
_BREAK_RE = re.compile(r"(?i)<br\s*/?>")
_TAG_RE = re.compile(r"<[^>]+>")
_BLANK_RUN_RE = re.compile(r"\n{3,}")
_TRAILING_SPACE_RE = re.compile(r"[ \t]+\n")


def html_to_plain_text(sanitized_html: str | None) -> str:
    """Reduce already-sanitized HTML to plain text.

    For AI prompt input, search indexing, and summaries — anywhere markup
    would either pollute a language model's input or make a substring search
    miss a word split across a tag boundary. Operates on HTML that has
    **already** been through :func:`sanitize_html`; this is a formatting
    reduction, not a second security boundary, so it is a plain tag strip
    rather than another pass through the cleaner.

    Block-level closing tags and ``<br>`` become newlines first, so
    paragraphs and list items do not run together into one unreadable line
    once the tags themselves are stripped.
    """
    if not sanitized_html:
        return ""
    text = _BLOCK_CLOSE_RE.sub("\n", sanitized_html)
    text = _BREAK_RE.sub("\n", text)
    text = _TAG_RE.sub("", text)
    text = _html_entities.unescape(text)
    text = _TRAILING_SPACE_RE.sub("\n", text)
    text = _BLANK_RUN_RE.sub("\n\n", text)
    return text.strip()


__all__ = ["html_to_plain_text", "sanitize_html"]
