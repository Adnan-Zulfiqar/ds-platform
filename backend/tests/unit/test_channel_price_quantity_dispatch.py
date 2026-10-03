"""Track E7 W3 — the after-commit hook queues every marketplace channel, and
only on commit."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

import pytest

from app.core.context import set_tenant_id
from app.tasks.integrations import channels
from app.tasks.integrations import ebay as ebay_tasks
from app.tasks.integrations import woocommerce as woocommerce_tasks

pytestmark = pytest.mark.unit


class _Session:
    sync_session = object()


def test_one_commit_queues_ebay_and_woocommerce_once_each(
    monkeypatch: pytest.MonkeyPatch, tenant_id: uuid.UUID
) -> None:
    listeners: list[tuple[str, Callable[[object], None]]] = []

    def listen(_target: Any, name: str, fn: Callable[[object], None], **_: Any) -> None:
        listeners.append((name, fn))

    queued: dict[str, list[list[uuid.UUID]]] = {"ebay": [], "woocommerce": []}
    monkeypatch.setattr(channels.event, "listen", listen)
    monkeypatch.setattr(
        ebay_tasks, "enqueue_price_quantity", lambda _t, ids: queued["ebay"].append(list(ids))
    )
    monkeypatch.setattr(
        woocommerce_tasks,
        "enqueue_price_quantity",
        lambda _t, ids: queued["woocommerce"].append(list(ids)),
    )
    set_tenant_id(tenant_id)
    a, b = uuid.uuid4(), uuid.uuid4()

    channels.push_price_quantity_after_commit(_Session(), [a, b, a])
    assert queued == {"ebay": [], "woocommerce": []}  # nothing before the commit

    [(name, on_commit)] = listeners
    assert name == "after_commit"
    on_commit(object())
    assert queued == {"ebay": [[a, b]], "woocommerce": [[a, b]]}


def test_no_products_registers_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[Any] = []
    monkeypatch.setattr(channels.event, "listen", lambda *a, **k: calls.append(a))
    channels.push_price_quantity_after_commit(_Session(), [])
    assert calls == []


def test_a_broker_outage_is_swallowed_for_woocommerce(monkeypatch: pytest.MonkeyPatch) -> None:
    def down(*_: Any, **__: Any) -> None:
        raise ConnectionError("broker down")

    monkeypatch.setattr(woocommerce_tasks.push_price_quantity, "delay", down)
    assert woocommerce_tasks.enqueue_price_quantity(uuid.uuid4(), [uuid.uuid4()]) == 0
