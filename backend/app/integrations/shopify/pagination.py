"""Bounded cursor pagination for Shopify GraphQL connections.

Shopify's Admin API is cursor-only — there is no page number and no total — so
every catalogue walk is a loop whose exit condition comes from the server. That
makes an unbounded loop the default failure mode, and against a large merchant
it is a self-inflicted outage: thousands of requests, a throttled app and a sync
that never finishes.

This module supplies the guards rather than the walking. GQL-1 migrates no
catalogue endpoint; GQL-3 onwards will, and when it does the bounds are already
here and already tested.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from app.integrations.shopify.exceptions import ShopifyPaginationError

#: Defaults, overridable per call. Chosen so an accident costs a bounded number
#: of requests rather than an afternoon of them.
DEFAULT_MAX_PAGES: Final = 100
DEFAULT_MAX_NODES: Final = 10_000


@dataclass(frozen=True, slots=True)
class PageInfo:
    """``pageInfo`` as Shopify returns it, validated."""

    has_next_page: bool
    end_cursor: str | None = None


def parse_page_info(value: object) -> PageInfo:
    """Read a ``pageInfo`` block, refusing a shape that cannot be paged safely.

    ``hasNextPage: true`` with no ``endCursor`` is the dangerous case: a naive
    loop re-requests the *first* page forever, quietly reprocessing the same
    nodes. It is rejected here rather than left to be discovered in production.
    """
    if not isinstance(value, Mapping):
        raise ShopifyPaginationError("Shopify connection response had no pageInfo object.")
    has_next = value.get("hasNextPage")
    if not isinstance(has_next, bool):
        raise ShopifyPaginationError("Shopify pageInfo.hasNextPage was missing or not a boolean.")
    cursor = value.get("endCursor")
    if cursor is not None and not isinstance(cursor, str):
        raise ShopifyPaginationError("Shopify pageInfo.endCursor was not a string.")
    if has_next and not cursor:
        raise ShopifyPaginationError(
            "Shopify reported another page but returned no endCursor; "
            "continuing would re-request the same page indefinitely."
        )
    return PageInfo(has_next_page=has_next, end_cursor=cursor)


class PageWalker:
    """Tracks one paginated traversal and enforces its bounds.

    Deliberately not a loop or a generator: the caller owns the request, this
    owns the decision to continue. That keeps the transport, the document and the
    node shape out of here, and means the same guard covers every connection.
    """

    __slots__ = ("_cursor", "_max_nodes", "_max_pages", "_nodes", "_pages", "_seen_cursors")

    def __init__(
        self,
        *,
        max_pages: int = DEFAULT_MAX_PAGES,
        max_nodes: int = DEFAULT_MAX_NODES,
    ) -> None:
        if max_pages < 1 or max_nodes < 1:
            raise ValueError("Pagination bounds must be at least 1.")
        self._max_pages = max_pages
        self._max_nodes = max_nodes
        self._pages = 0
        self._nodes = 0
        self._cursor: str | None = None
        self._seen_cursors: set[str] = set()

    @property
    def cursor(self) -> str | None:
        """The ``after:`` value for the next request; ``None`` for the first."""
        return self._cursor

    @property
    def pages_fetched(self) -> int:
        return self._pages

    @property
    def nodes_seen(self) -> int:
        return self._nodes

    def record(self, *, page_info: PageInfo, node_count: int) -> bool:
        """Account for one page and answer whether to request another.

        Raises rather than stopping quietly when a bound is hit: a truncated
        catalogue sync that *looks* complete is worse than a loud failure,
        because the missing half is invisible.
        """
        if node_count < 0:
            raise ShopifyPaginationError("A page cannot contain a negative number of nodes.")
        self._pages += 1
        self._nodes += node_count

        if self._nodes > self._max_nodes:
            raise ShopifyPaginationError(
                f"Shopify pagination exceeded its node budget ({self._max_nodes})."
            )
        if not page_info.has_next_page:
            self._cursor = None
            return False
        if self._pages >= self._max_pages:
            raise ShopifyPaginationError(
                f"Shopify pagination exceeded its page budget ({self._max_pages})."
            )

        cursor = page_info.end_cursor
        # `parse_page_info` already refused has_next with no cursor; this is the
        # belt to that braces, and keeps `record` safe if a caller builds a
        # PageInfo by hand.
        if not cursor:
            raise ShopifyPaginationError("Shopify reported another page but returned no endCursor.")
        if cursor in self._seen_cursors:
            # A repeated cursor means the server is handing back a page already
            # walked. Continuing is an infinite loop with extra steps.
            raise ShopifyPaginationError(
                "Shopify returned a cursor this traversal has already followed."
            )
        self._seen_cursors.add(cursor)
        self._cursor = cursor
        return True


def connection_nodes(connection: object) -> list[Any]:
    """Read ``nodes`` from a connection, tolerating the ``edges`` form.

    Shopify exposes both; documents in this codebase use ``nodes``, but a
    hand-written one that used ``edges { node }`` should not silently yield an
    empty page.
    """
    if not isinstance(connection, Mapping):
        raise ShopifyPaginationError("Shopify connection response was not an object.")
    nodes = connection.get("nodes")
    if isinstance(nodes, list):
        return list(nodes)
    edges = connection.get("edges")
    if isinstance(edges, list):
        return [edge.get("node") for edge in edges if isinstance(edge, Mapping)]
    raise ShopifyPaginationError("Shopify connection response contained neither nodes nor edges.")


__all__ = [
    "DEFAULT_MAX_NODES",
    "DEFAULT_MAX_PAGES",
    "PageInfo",
    "PageWalker",
    "connection_nodes",
    "parse_page_info",
]
