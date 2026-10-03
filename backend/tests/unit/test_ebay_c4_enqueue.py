"""EBAY-C4 enqueue helper — no broker, no database."""

from __future__ import annotations

import uuid

import pytest

from app.tasks.integrations import ebay as ebay_tasks

pytestmark = pytest.mark.unit


def test_each_product_is_queued_once(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(ebay_tasks.push_price_quantity, "delay", lambda t, p: sent.append((t, p)))
    tenant = uuid.uuid4()
    a, b = uuid.uuid4(), uuid.uuid4()

    assert ebay_tasks.enqueue_price_quantity(tenant, [a, b, a]) == 2
    assert sent == [(str(tenant), str(a)), (str(tenant), str(b))]


def test_a_broker_outage_does_not_fail_the_caller(monkeypatch: pytest.MonkeyPatch) -> None:
    def down(*_: object) -> None:
        raise ConnectionError("broker unreachable")

    monkeypatch.setattr(ebay_tasks.push_price_quantity, "delay", down)

    assert ebay_tasks.enqueue_price_quantity(uuid.uuid4(), [uuid.uuid4()]) == 0
