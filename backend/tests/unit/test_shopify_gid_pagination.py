"""GQL-1 — Shopify GID parsing and bounded cursor pagination.

Both exist to stop a specific class of migration bug rather than to be generally
useful: identity derived from array position, and a catalogue walk with no exit.
The tests are written as the guarantees those helpers owe their callers.
"""

from __future__ import annotations

import pytest

from app.integrations.shopify.exceptions import ShopifyGidError, ShopifyPaginationError
from app.integrations.shopify.gid import ShopifyGid, parse_gid
from app.integrations.shopify.pagination import (
    PageInfo,
    PageWalker,
    connection_nodes,
    parse_page_info,
)

pytestmark = pytest.mark.unit


class TestGidParsing:
    @pytest.mark.parametrize(
        ("raw", "resource", "numeric"),
        [
            ("gid://shopify/Product/1234567890", "Product", "1234567890"),
            ("gid://shopify/ProductVariant/42", "ProductVariant", "42"),
            ("gid://shopify/InventoryItem/999", "InventoryItem", "999"),
            ("gid://shopify/Location/7", "Location", "7"),
            ("gid://shopify/WebhookSubscription/8", "WebhookSubscription", "8"),
        ],
    )
    def test_valid_gids_parse(self, raw: str, resource: str, numeric: str) -> None:
        gid = parse_gid(raw)
        assert gid == ShopifyGid(value=raw, resource=resource, numeric_id=numeric)

    def test_the_complete_gid_is_retained(self) -> None:
        """What gets persisted is the whole identifier, not the trailing number:
        a bare id is ambiguous across resource types."""
        raw = "gid://shopify/ProductVariant/42"
        gid = parse_gid(raw)
        assert gid.value == raw
        assert str(gid) == raw

    @pytest.mark.parametrize(
        "rejected",
        [
            "",
            "   ",
            "1234567890",
            "gid://shopify/Product/",
            "gid://shopify//1234",
            "gid://shopify/Product",
            "gid://other/Product/1",
            "https://shopify.com/Product/1",
            "gid://shopify/Product/1/extra",
            "gid://shopify/Product/1?x=2",
            "gid://shopify/Product/1#f",
            "gid://shopify/Product/ 1",
            "gid://shopify/9Product/1",
        ],
    )
    def test_malformed_identifiers_are_refused(self, rejected: str) -> None:
        with pytest.raises(ShopifyGidError):
            parse_gid(rejected)

    @pytest.mark.parametrize("rejected", [None, 1234, 12.5, ["gid://shopify/Product/1"], {}])
    def test_non_string_identifiers_are_refused(self, rejected: object) -> None:
        """An int arriving here is a REST id leaking into a GraphQL path."""
        with pytest.raises(ShopifyGidError):
            parse_gid(rejected)

    def test_the_expected_resource_type_is_enforced(self) -> None:
        """Both parse; only an explicit expectation catches the swap.

        Passing a ProductVariant GID where an InventoryItem was meant is the
        mistake this guard exists for, and it is invisible without it.
        """
        with pytest.raises(ShopifyGidError):
            parse_gid("gid://shopify/ProductVariant/1", expected_resource="InventoryItem")

        gid = parse_gid("gid://shopify/InventoryItem/1", expected_resource="InventoryItem")
        assert gid.resource == "InventoryItem"

    def test_identity_never_comes_from_a_position(self) -> None:
        """The blueprint guard, asserted rather than trusted.

        A previous generation derived variant identity from array position, so a
        reordered response repriced the wrong variant. There is no API here that
        accepts an index.
        """
        gids = [
            parse_gid("gid://shopify/ProductVariant/900"),
            parse_gid("gid://shopify/ProductVariant/100"),
        ]
        assert [g.numeric_id for g in gids] == ["900", "100"]
        with pytest.raises(ShopifyGidError):
            parse_gid(0)


class TestPageInfoParsing:
    def test_a_normal_page_parses(self) -> None:
        info = parse_page_info({"hasNextPage": True, "endCursor": "abc"})
        assert info == PageInfo(has_next_page=True, end_cursor="abc")

    def test_a_terminal_page_parses_without_a_cursor(self) -> None:
        info = parse_page_info({"hasNextPage": False, "endCursor": None})
        assert info.has_next_page is False

    def test_a_next_page_without_a_cursor_is_refused(self) -> None:
        """The dangerous shape: a naive loop would re-request page one forever,
        silently reprocessing the same nodes."""
        with pytest.raises(ShopifyPaginationError):
            parse_page_info({"hasNextPage": True, "endCursor": None})
        with pytest.raises(ShopifyPaginationError):
            parse_page_info({"hasNextPage": True})

    @pytest.mark.parametrize(
        "rejected",
        [None, [], "pageInfo", {"hasNextPage": "yes"}, {"endCursor": "abc"}],
    )
    def test_a_malformed_page_info_is_refused(self, rejected: object) -> None:
        with pytest.raises(ShopifyPaginationError):
            parse_page_info(rejected)

    def test_a_non_string_cursor_is_refused(self) -> None:
        with pytest.raises(ShopifyPaginationError):
            parse_page_info({"hasNextPage": True, "endCursor": 42})


class TestPageWalker:
    def test_a_normal_traversal_advances_and_terminates(self) -> None:
        walker = PageWalker()
        assert walker.cursor is None

        assert walker.record(page_info=PageInfo(True, "c1"), node_count=25) is True
        assert walker.cursor == "c1"

        assert walker.record(page_info=PageInfo(False, None), node_count=10) is False
        assert walker.cursor is None
        assert walker.pages_fetched == 2
        assert walker.nodes_seen == 35

    def test_the_page_budget_is_enforced(self) -> None:
        walker = PageWalker(max_pages=2)
        walker.record(page_info=PageInfo(True, "c1"), node_count=1)
        with pytest.raises(ShopifyPaginationError, match="page budget"):
            walker.record(page_info=PageInfo(True, "c2"), node_count=1)

    def test_the_node_budget_is_enforced(self) -> None:
        walker = PageWalker(max_nodes=30)
        walker.record(page_info=PageInfo(True, "c1"), node_count=25)
        with pytest.raises(ShopifyPaginationError, match="node budget"):
            walker.record(page_info=PageInfo(True, "c2"), node_count=10)

    def test_a_repeated_cursor_is_refused(self) -> None:
        """A cursor already followed means the server is handing back a page the
        traversal has walked. Continuing is an infinite loop with extra steps."""
        walker = PageWalker()
        walker.record(page_info=PageInfo(True, "c1"), node_count=1)
        with pytest.raises(ShopifyPaginationError, match="already followed"):
            walker.record(page_info=PageInfo(True, "c1"), node_count=1)

    def test_a_missing_cursor_is_refused_even_when_built_by_hand(self) -> None:
        walker = PageWalker()
        with pytest.raises(ShopifyPaginationError):
            walker.record(page_info=PageInfo(True, None), node_count=1)

    def test_a_negative_node_count_is_refused(self) -> None:
        walker = PageWalker()
        with pytest.raises(ShopifyPaginationError):
            walker.record(page_info=PageInfo(False, None), node_count=-1)

    def test_bounds_must_be_positive(self) -> None:
        with pytest.raises(ValueError):
            PageWalker(max_pages=0)
        with pytest.raises(ValueError):
            PageWalker(max_nodes=0)

    def test_exhausting_a_budget_raises_rather_than_stopping_quietly(self) -> None:
        """A truncated sync that *looks* complete is worse than a loud failure,
        because the missing half is invisible."""
        walker = PageWalker(max_pages=1)
        with pytest.raises(ShopifyPaginationError):
            walker.record(page_info=PageInfo(True, "c1"), node_count=1)


class TestConnectionNodes:
    def test_the_nodes_form_is_read(self) -> None:
        assert connection_nodes({"nodes": [{"id": "a"}, {"id": "b"}]}) == [{"id": "a"}, {"id": "b"}]

    def test_the_edges_form_is_read(self) -> None:
        connection = {"edges": [{"node": {"id": "a"}}, {"node": {"id": "b"}}]}
        assert connection_nodes(connection) == [{"id": "a"}, {"id": "b"}]

    def test_an_empty_connection_is_valid(self) -> None:
        assert connection_nodes({"nodes": []}) == []

    @pytest.mark.parametrize("rejected", [None, [], "nodes", {"items": []}])
    def test_a_connection_with_neither_form_is_refused(self, rejected: object) -> None:
        with pytest.raises(ShopifyPaginationError):
            connection_nodes(rejected)
