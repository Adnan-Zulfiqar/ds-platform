"""Canonical fingerprint for pipeline bulk run idempotency."""

from __future__ import annotations

import uuid

import pytest

from app.services.pipeline_bulk import pipeline_bulk_fingerprint

pytestmark = pytest.mark.unit


def test_duplicate_ids_do_not_change_the_fingerprint() -> None:
    first = uuid.uuid4()
    second = uuid.uuid4()
    once = pipeline_bulk_fingerprint(
        product_ids=[first, second], tone="professional", store_id=None
    )
    twice = pipeline_bulk_fingerprint(
        product_ids=[first, first, second, first],
        tone="professional",
        store_id=None,
    )
    assert once == twice
    assert len(once) == 64


def test_order_does_not_change_the_fingerprint() -> None:
    a = uuid.uuid4()
    b = uuid.uuid4()
    assert pipeline_bulk_fingerprint(
        product_ids=[a, b], tone="professional", store_id=None
    ) == pipeline_bulk_fingerprint(product_ids=[b, a], tone="professional", store_id=None)


def test_tone_and_store_are_part_of_the_fingerprint() -> None:
    product = uuid.uuid4()
    store = uuid.uuid4()
    base = pipeline_bulk_fingerprint(product_ids=[product], tone="professional", store_id=None)
    other_tone = pipeline_bulk_fingerprint(product_ids=[product], tone="casual", store_id=None)
    with_store = pipeline_bulk_fingerprint(
        product_ids=[product], tone="professional", store_id=store
    )
    assert base != other_tone
    assert base != with_store
    assert other_tone != with_store
