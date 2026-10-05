"""Track E7 W3 — the after-commit hook queues every marketplace channel, and
only on commit. Shopify joined the fan-out when the analysis of 2026-10-04
found its push tasks were never enqueued."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

import pytest

from app.core.context import set_tenant_id
from app.tasks.integrations import channels
from app.tasks.integrations import ebay as ebay_tasks
from app.tasks.integrations import shopify as shopify_tasks
from app.tasks.integrations import woocommerce as woocommerce_tasks

pytestmark = pytest.mark.unit

CHANNELS = ("ebay", "woocommerce", "shopify")


class _Session:
    sync_session = object()


@pytest.fixture
def queued(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[list[uuid.UUID]]]:
    """Every channel's enqueue replaced by a recorder, so the test sees the
    fan-out without a broker."""
    record: dict[str, list[list[uuid.UUID]]] = {name: [] for name in CHANNELS}
    for name, module in (
        ("ebay", ebay_tasks),
        ("woocommerce", woocommerce_tasks),
        ("shopify", shopify_tasks),
    ):

        def enqueue(_t: uuid.UUID, ids: Any, *, _name: str = name) -> int:
            record[_name].append(list(ids))
            return len(record[_name][-1])

        monkeypatch.setattr(module, "enqueue_price_quantity", enqueue)
    return record


def test_one_commit_queues_every_channel_once(
    monkeypatch: pytest.MonkeyPatch,
    tenant_id: uuid.UUID,
    queued: dict[str, list[list[uuid.UUID]]],
) -> None:
    listeners: list[tuple[str, Callable[[object], None]]] = []

    def listen(_target: Any, name: str, fn: Callable[[object], None], **_: Any) -> None:
        listeners.append((name, fn))

    monkeypatch.setattr(channels.event, "listen", listen)
    set_tenant_id(tenant_id)
    a, b = uuid.uuid4(), uuid.uuid4()

    channels.push_price_quantity_after_commit(_Session(), [a, b, a])
    assert queued == {name: [] for name in CHANNELS}  # nothing before the commit

    [(name, on_commit)] = listeners
    assert name == "after_commit"
    on_commit(object())
    assert queued == {name: [[a, b]] for name in CHANNELS}


def test_the_direct_fan_out_reports_each_channel(queued: dict[str, list[list[uuid.UUID]]]) -> None:
    a = uuid.uuid4()
    assert channels.enqueue_price_quantity(uuid.uuid4(), [a, a]) == {
        "ebay": 1,
        "woocommerce": 1,
        "shopify": 1,
    }
    assert queued == {name: [[a]] for name in CHANNELS}


def test_no_products_registers_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[Any] = []
    monkeypatch.setattr(channels.event, "listen", lambda *a, **k: calls.append(a))
    channels.push_price_quantity_after_commit(_Session(), [])
    assert calls == []
    assert channels.enqueue_price_quantity(uuid.uuid4(), []) == {}


@pytest.mark.parametrize("module", [woocommerce_tasks, shopify_tasks])
def test_a_broker_outage_is_swallowed(monkeypatch: pytest.MonkeyPatch, module: Any) -> None:
    def down(*_: Any, **__: Any) -> None:
        raise ConnectionError("broker down")

    monkeypatch.setattr(module.push_price_quantity, "delay", down)
    assert module.enqueue_price_quantity(uuid.uuid4(), [uuid.uuid4()]) == 0
