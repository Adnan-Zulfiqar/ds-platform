"""Review finding G-2 — the first preview must not move the draft's token.

Committed transactions throughout: inside one shared test transaction
Postgres ``now()`` is frozen, which would hide exactly the bump this guards
against.
"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa

from app.models.product import ProductVersion, ProductVersionSource
from app.repositories.product import ProductRepository
from app.services.image_analysis import ImageAnalysisReport, ImageAnalysisService
from app.services.product import ProductService
from app.services.product_optimization import ProductOptimizationService
from app.services.product_pipeline import ProductPipelineService
from tests.integration.shopify_publish_live import live_publish_targets, own_publish_session

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _skip_image_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _empty(
        self: ImageAnalysisService,
        product_id: uuid.UUID,
        *,
        executed_by_user_id: uuid.UUID | None = None,
    ) -> ImageAnalysisReport:
        return ImageAnalysisReport(product_id=product_id, images=())

    monkeypatch.setattr(ImageAnalysisService, "analyse_product_images", _empty)


class TestFirstPreviewKeepsTheDraftToken:
    async def test_first_preview_records_the_snapshot_without_moving_updated_at(self) -> None:
        async with live_publish_targets() as (live,):
            token_before = live.updated_at
            async with own_publish_session(live) as session:
                preview = await ProductPipelineService(session).preview(
                    live.product_id, requested_by_user_id=None
                )
                await session.commit()

            async with own_publish_session(live) as session:
                product = await ProductRepository(session).get_by_id_or_raise(live.product_id)
                versions = (
                    (
                        await session.execute(
                            sa.select(ProductVersion)
                            .where(ProductVersion.product_id == live.product_id)
                            .order_by(ProductVersion.version_number)
                        )
                    )
                    .scalars()
                    .all()
                )
                token_after = product.updated_at
                ai_version = product.ai_version

        assert token_after == token_before
        assert ai_version == 1
        assert versions[0].source is ProductVersionSource.ORIGINAL
        assert versions[0].active is True
        assert preview.candidate_active is False
        assert preview.source_updated_at == token_before
        assert preview.approval_expected_updated_at == token_before

    async def test_an_open_editor_can_still_save_with_its_token_after_a_preview(self) -> None:
        async with live_publish_targets() as (live,):
            editor_token = live.updated_at
            async with own_publish_session(live) as session:
                await ProductPipelineService(session).preview(
                    live.product_id, requested_by_user_id=None
                )
                await session.commit()

            async with own_publish_session(live) as session:
                saved = await ProductService(session).update_draft(
                    live.product_id,
                    {"title": "Merchant kept typing"},
                    expected_updated_at=editor_token,
                )
                await session.commit()
                saved_title = saved.title

        assert saved_title == "Merchant kept typing"

    async def test_activation_still_moves_the_token(self) -> None:
        """Only the snapshot bookkeeping is exempt; a real activation is a change."""
        async with live_publish_targets() as (live,):
            token_before = live.updated_at
            async with own_publish_session(live) as session:
                await ProductOptimizationService(session).optimize_product(
                    live.product_id, requested_by_user_id=None
                )
                await session.commit()
            async with own_publish_session(live) as session:
                product = await ProductRepository(session).get_by_id_or_raise(live.product_id)
                token_after = product.updated_at

        assert token_after > token_before
