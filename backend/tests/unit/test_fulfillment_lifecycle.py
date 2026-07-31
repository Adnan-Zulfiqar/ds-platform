"""Unit tests for the fulfilment lifecycle and supplier status mapping."""

from __future__ import annotations

from itertools import pairwise

import pytest

from app.models.order import (
    FULFILLMENT_TRANSITIONS,
    FulfillmentStatus,
    PaymentStatus,
    ShipmentStatus,
    can_transition,
)
from app.services.order_sync import (
    map_fulfillment_status,
    map_payment_status,
    map_shipment_status,
)

pytestmark = pytest.mark.unit


class TestTransitionMap:
    def test_every_status_appears_in_the_map(self) -> None:
        """A status missing from the map would make every move out of it
        illegal by accident rather than by decision."""
        assert set(FULFILLMENT_TRANSITIONS) == set(FulfillmentStatus)

    def test_the_happy_path_is_fully_traversable(self) -> None:
        path = [
            FulfillmentStatus.PENDING,
            FulfillmentStatus.AWAITING_PAYMENT,
            FulfillmentStatus.PAID,
            FulfillmentStatus.PROCESSING,
            FulfillmentStatus.FULFILLED,
            FulfillmentStatus.SHIPPED,
            FulfillmentStatus.DELIVERED,
        ]
        for current, target in pairwise(path):
            assert can_transition(current, target), f"{current} -> {target} must be legal"

    def test_cancelled_and_refunded_are_terminal(self) -> None:
        for terminal in (FulfillmentStatus.CANCELLED, FulfillmentStatus.REFUNDED):
            for target in FulfillmentStatus:
                if target == terminal:
                    continue
                assert not can_transition(terminal, target), (
                    f"{terminal} is terminal; {target} must be unreachable"
                )

    def test_a_delivered_order_cannot_unship(self) -> None:
        assert not can_transition(FulfillmentStatus.DELIVERED, FulfillmentStatus.SHIPPED)
        assert not can_transition(FulfillmentStatus.DELIVERED, FulfillmentStatus.PENDING)

    def test_a_delivered_order_can_still_be_disputed_or_refunded(self) -> None:
        """Chargebacks arrive after delivery; the lifecycle must allow them."""
        assert can_transition(FulfillmentStatus.DELIVERED, FulfillmentStatus.DISPUTED)
        assert can_transition(FulfillmentStatus.DELIVERED, FulfillmentStatus.REFUNDED)

    def test_disputes_can_resolve_in_three_directions(self) -> None:
        assert can_transition(FulfillmentStatus.DISPUTED, FulfillmentStatus.REFUNDED)
        assert can_transition(FulfillmentStatus.DISPUTED, FulfillmentStatus.CANCELLED)
        assert can_transition(FulfillmentStatus.DISPUTED, FulfillmentStatus.DELIVERED)

    def test_restating_the_current_status_is_permitted(self) -> None:
        """A sync confirming the current state is a no-op, not a violation."""
        for status in FulfillmentStatus:
            assert can_transition(status, status)

    def test_skipping_the_happy_path_backwards_is_illegal(self) -> None:
        assert not can_transition(FulfillmentStatus.SHIPPED, FulfillmentStatus.PAID)
        assert not can_transition(FulfillmentStatus.PROCESSING, FulfillmentStatus.PENDING)


class TestSupplierStatusMapping:
    """Documentation-derived vocabulary (M16). The raw supplier value is stored
    alongside the mapped one, so a wrong mapping is recoverable — these tests
    pin the mapping that was chosen, not AliExpress's behaviour."""

    def test_the_documented_lifecycle_maps_forward(self) -> None:
        assert map_fulfillment_status("PLACE_ORDER_SUCCESS") is FulfillmentStatus.AWAITING_PAYMENT
        assert map_fulfillment_status("WAIT_SELLER_SEND_GOODS") is FulfillmentStatus.PROCESSING
        assert map_fulfillment_status("WAIT_BUYER_ACCEPT_GOODS") is FulfillmentStatus.SHIPPED
        assert map_fulfillment_status("FINISH") is FulfillmentStatus.DELIVERED

    def test_case_and_whitespace_are_normalised(self) -> None:
        assert map_fulfillment_status("  finish ") is FulfillmentStatus.DELIVERED

    def test_an_unknown_status_maps_to_none_not_a_guess(self) -> None:
        """An unmapped status keeps the order's current state and appears in
        the timeline verbatim — guessing would hide exactly the case a human
        should look at."""
        assert map_fulfillment_status("SOMETHING_NEW") is None
        assert map_fulfillment_status(None) is None
        assert map_fulfillment_status("") is None

    def test_payment_status_follows_the_order_status(self) -> None:
        assert map_payment_status("WAIT_SELLER_SEND_GOODS") is PaymentStatus.PAID
        assert map_payment_status("PLACE_ORDER_SUCCESS") is PaymentStatus.UNPAID
        assert map_payment_status("SOMETHING_NEW") is PaymentStatus.UNKNOWN
        assert map_payment_status(None) is PaymentStatus.UNKNOWN

    def test_shipment_status_mapping(self) -> None:
        assert map_shipment_status("SELLER_SEND_GOODS") is ShipmentStatus.IN_TRANSIT
        assert map_shipment_status("BUYER_ACCEPT_GOODS") is ShipmentStatus.DELIVERED
        assert map_shipment_status("NO_LOGISTICS") is ShipmentStatus.UNKNOWN
        assert map_shipment_status(None) is ShipmentStatus.UNKNOWN
        assert map_shipment_status("NEVER_SEEN") is ShipmentStatus.UNKNOWN
