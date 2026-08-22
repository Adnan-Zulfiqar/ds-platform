"""Shopify global identifiers, including the official parameterized form.

A GID is the only durable name Shopify gives a resource, and it is the one thing
this migration must never get creative with. The blueprint guard is blunt about
why: a previous generation of this integration derived variant identity from
*array position*, so a reordered response silently repriced the wrong variant.
Positions are not identity.

**Two documented shapes**, both supported here:

* ``gid://shopify/{object_name}/{id}`` — e.g. ``gid://shopify/Product/123``
* ``gid://shopify/{child}/{child_id}?{parent}_id={parent_id}`` — the official
  example is ``gid://shopify/InventoryLevel/123?inventory_item_id=456``

The first version of this parser terminated the id at ``?`` and rejected every
parameterized GID outright, which would have failed on GQL-4's first inventory
call. Nothing here is specific to ``InventoryLevel``: Shopify documents one
parameter today and promises nothing about tomorrow, so the *syntax* is
validated and the semantics are left to Shopify.

## Persistence contract

**The complete, opaque GID string is the authority.** ``value`` is exactly what
Shopify sent — same characters, same parameter order, same percent-encoding —
and that is what must be stored and what must be sent back. ``resource``,
``numeric_id`` and ``parameters`` are *validation and convenience views*: safe to
read, log or branch on, never a basis for reconstructing an identifier. A
parameterized GID rebuilt from its parts would be a different string, and for
``InventoryLevel`` a different string addresses a different inventory level.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Final
from urllib.parse import unquote

from app.integrations.shopify.exceptions import ShopifyGidError

#: ``gid://shopify/{Resource}/{id}`` with an optional ``?query``.
#:
#: The authority is pinned to a bare ``shopify`` -- no userinfo, no port, no
#: alternative host -- because everything after it is trusted as an identifier.
#: The id stops at ``/``, ``?`` or ``#``; the query, when present, is validated
#: separately rather than swallowed as "whatever came after the question mark".
_GID_PATTERN: Final = re.compile(
    r"^gid://shopify/(?P<resource>[A-Za-z][A-Za-z0-9]*)/(?P<id>[^/?#]+)(?:\?(?P<query>[^#]*))?$"
)

#: Parameter keys: the documented form is ``{parent_object_name}_id``, but the
#: rule enforced is syntactic rather than semantic so a future Shopify parameter
#: is not rejected for failing to look like today's.
_PARAM_KEY: Final = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

#: Parameter values: URL-unreserved characters plus percent-escapes. Deliberately
#: narrow -- an arbitrary query string is not automatically safe just because it
#: arrived attached to a GID.
_PARAM_VALUE: Final = re.compile(r"^(?:[A-Za-z0-9._~\-]|%[0-9A-Fa-f]{2})+$")

#: Anything in this class is refused anywhere in the identifier. A control
#: character in a stored identifier is a log-injection and a comparison hazard.
_FORBIDDEN: Final = re.compile(r"[\s\x00-\x1f\x7f]")


@dataclass(frozen=True, slots=True)
class ShopifyGid:
    """A parsed, validated ``gid://shopify/...`` identifier.

    ``value`` is the **complete** GID exactly as Shopify sent it, and that is
    what gets persisted. ``resource``, ``numeric_id`` and ``parameters`` are
    views over it for validation and logging — never a way to rebuild it.
    """

    value: str
    resource: str
    numeric_id: str
    #: Stored as an ordered tuple rather than a dict so the dataclass stays
    #: hashable and comparable; ``parameters`` exposes the read-only mapping.
    _parameters: tuple[tuple[str, str], ...] = field(default=())

    def __str__(self) -> str:
        """The whole identifier, parameters included. Never the base id alone."""
        return self.value

    @property
    def parameters(self) -> MappingProxyType[str, str]:
        """Decoded parameters, read-only.

        Percent-escapes are decoded here for inspection; ``value`` keeps the
        original encoding, so nothing is ever re-encoded on the way back to
        Shopify.
        """
        return MappingProxyType(dict(self._parameters))

    @property
    def is_parameterized(self) -> bool:
        return bool(self._parameters)

    def expect(self, resource: str) -> ShopifyGid:
        """Assert the resource type, returning self so calls can be chained.

        Guards the mistake this module was written for: passing a
        ``ProductVariant`` GID where an ``InventoryItem`` was meant. Both are
        valid GIDs and both parse, so only an explicit expectation catches it.
        """
        if self.resource != resource:
            raise ShopifyGidError(
                f"Expected a Shopify {resource} identifier but received a {self.resource}."
            )
        return self


def _parse_parameters(query: str) -> tuple[tuple[str, str], ...]:
    """Validate and decode a GID query component.

    Refuses rather than repairs. An empty query, an empty pair, a bare key, an
    empty value, a duplicate key or an invalid percent-escape are all rejected:
    each has more than one plausible reading, and for an identifier that
    addresses a merchant's inventory, guessing which one Shopify meant is not an
    option a client should have.
    """
    if not query:
        # `gid://shopify/X/1?` -- a question mark introducing nothing.
        raise ShopifyGidError("Shopify GID has an empty parameter list.")

    seen: dict[str, str] = {}
    pairs: list[tuple[str, str]] = []
    for part in query.split("&"):
        if not part:
            raise ShopifyGidError("Shopify GID contains an empty parameter.")
        key, separator, raw_value = part.partition("=")
        if not separator:
            raise ShopifyGidError(f"Shopify GID parameter {part!r} has no value.")
        if not _PARAM_KEY.match(key):
            raise ShopifyGidError(f"Shopify GID parameter name {key!r} is not valid.")
        if not raw_value:
            raise ShopifyGidError(f"Shopify GID parameter {key!r} has an empty value.")
        if not _PARAM_VALUE.match(raw_value):
            raise ShopifyGidError(
                f"Shopify GID parameter {key!r} has an invalid or badly encoded value."
            )
        if key in seen:
            # Two readings address two different objects. Collapsing them would
            # pick one silently.
            raise ShopifyGidError(f"Shopify GID repeats the parameter {key!r}.")
        decoded = unquote(raw_value, errors="strict")
        seen[key] = decoded
        pairs.append((key, decoded))
    return tuple(pairs)


def parse_gid(value: object, *, expected_resource: str | None = None) -> ShopifyGid:
    """Parse a Shopify GID, refusing anything that is not one.

    Deliberately strict. A GID arriving as ``None``, an int, an empty string, a
    bare numeric id or a URL is a bug at the call site, and returning something
    plausible would push the failure downstream to a mutation that mis-targets a
    merchant's catalogue.
    """
    if not isinstance(value, str):
        raise ShopifyGidError("A Shopify identifier must be a string.")
    if not value:
        raise ShopifyGidError("A Shopify identifier cannot be empty.")
    if _FORBIDDEN.search(value):
        # Checked before anything else and on the *original* string: stripping
        # first would accept an identifier that differs from the one stored.
        raise ShopifyGidError(
            "A Shopify identifier cannot contain whitespace or control characters."
        )

    match = _GID_PATTERN.match(value)
    if match is None:
        raise ShopifyGidError(
            "Value is not a gid://shopify/{Resource}/{id} identifier, "
            "optionally followed by ?name=value parameters."
        )

    query = match.group("query")
    parameters = _parse_parameters(query) if query is not None else ()

    gid = ShopifyGid(
        # The original string, untouched. Never rebuilt from the parts below.
        value=value,
        resource=match.group("resource"),
        numeric_id=match.group("id"),
        _parameters=parameters,
    )
    if expected_resource is not None:
        gid.expect(expected_resource)
    return gid


__all__ = ["ShopifyGid", "parse_gid"]
