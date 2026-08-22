"""Shopify global identifiers.

A GID is the only durable name Shopify gives a resource, and it is the one thing
this migration must never get creative with. The blueprint guard is blunt about
why: a previous generation of this integration derived variant identity from
*array position*, so a reordered response silently repriced the wrong variant.
Positions are not identity. This module exists so that nothing downstream is
tempted to reconstruct one.

Kept separate from ``graphql.py`` because it is a value object with no transport
concerns: operations, tests and (later) repositories all need it, and none of
them should have to import an HTTP client to parse an identifier.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.integrations.shopify.exceptions import ShopifyGidError

#: ``gid://shopify/{Resource}/{id}``. Resource is PascalCase; the trailing id is
#: numeric for every Admin resource this platform touches, but is kept as text
#: because Shopify does not promise that and an int round-trip would lose
#: leading characters if it ever changed.
_GID_PATTERN = re.compile(r"^gid://shopify/(?P<resource>[A-Za-z][A-Za-z0-9]*)/(?P<id>[^/?#\s]+)$")


@dataclass(frozen=True, slots=True)
class ShopifyGid:
    """A parsed, validated ``gid://shopify/...`` identifier.

    ``value`` is the **complete** GID exactly as Shopify sent it, and that is
    what gets persisted. ``resource`` and ``numeric_id`` are conveniences for
    logging and for the rare REST bridge that still needs the legacy id — never
    a substitute for the whole thing, because a bare id is ambiguous across
    resource types.
    """

    value: str
    resource: str
    numeric_id: str

    def __str__(self) -> str:
        return self.value

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


def parse_gid(value: object, *, expected_resource: str | None = None) -> ShopifyGid:
    """Parse a Shopify GID, refusing anything that is not one.

    Deliberately strict. A GID arriving as ``None``, an int, an empty string, a
    bare numeric id or a URL is a bug at the call site, and returning something
    plausible would push the failure downstream to a mutation that mis-targets a
    merchant's catalogue.
    """
    if not isinstance(value, str):
        raise ShopifyGidError("A Shopify identifier must be a string.")
    candidate = value.strip()
    if not candidate:
        raise ShopifyGidError("A Shopify identifier cannot be empty.")
    match = _GID_PATTERN.match(candidate)
    if match is None:
        raise ShopifyGidError("Value is not a gid://shopify/{Resource}/{id} identifier.")
    gid = ShopifyGid(
        value=candidate,
        resource=match.group("resource"),
        numeric_id=match.group("id"),
    )
    if expected_resource is not None:
        gid.expect(expected_resource)
    return gid


__all__ = ["ShopifyGid", "parse_gid"]
