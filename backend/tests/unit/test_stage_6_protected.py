"""Stage 6 must not leak into API schemas, routes, optimize, or frontend types."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from app.api.v1.router import api_router
from app.schemas.product import ProductDetailRead, ProductImageRead
from app.services.product_optimization import (
    _DESCRIPTION_PROMPT,
    _SEO_PROMPT,
    _TITLE_PROMPT,
    ProductOptimizationService,
)

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = Path(__file__).resolve().parents[2]


class TestStage6ProtectedBoundaries:
    def test_product_image_read_has_no_analysis_field(self) -> None:
        assert "analysis" not in ProductImageRead.model_fields
        assert set(ProductImageRead.model_fields) == {
            "id",
            "url",
            "position",
            "alt_text",
            "is_supplier",
        }

    def test_product_detail_read_has_no_analysis_field(self) -> None:
        assert "analysis" not in ProductDetailRead.model_fields

    def test_no_image_analysis_endpoint(self) -> None:
        paths = [getattr(route, "path", "") for route in api_router.routes]
        joined = " ".join(paths)
        assert "/image-analysis" not in joined
        assert "analyse-images" not in joined
        assert "analyze-images" not in joined
        assert not (BACKEND_ROOT / "app/api/v1/image_analysis").exists()
        assert not (BACKEND_ROOT / "app/api/v1/image_analysis.py").exists()

    def test_optimize_prompt_names_are_exactly_stage_4(self) -> None:
        assert {_TITLE_PROMPT, _DESCRIPTION_PROMPT, _SEO_PROMPT} == {
            "product_title_generator",
            "product_description_generator",
            "seo_optimizer",
        }
        source = inspect.getsource(ProductOptimizationService)
        tree = ast.parse(source)
        called: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and node.value in {
                "image_analyzer",
                "quality_scorer",
                "product_title_generator",
                "product_description_generator",
                "seo_optimizer",
            }:
                called.add(str(node.value))
        assert "image_analyzer" not in source
        assert "quality_scorer" not in source
        assert called <= {
            "product_title_generator",
            "product_description_generator",
            "seo_optimizer",
        }

    def test_frontend_product_image_has_no_analysis(self) -> None:
        text = (REPO_ROOT / "frontend" / "types" / "api.ts").read_text(encoding="utf-8")
        start = text.index("export interface ProductImage {")
        end = text.index("}", start)
        block = text[start:end]
        assert "analysis" not in block
        assert "altText" in block

    def test_ci_expected_alembic_head_is_0040(self) -> None:
        text = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        assert 'current" != "0040"' in text
        assert "Expected Alembic head 0040" in text
        assert "Expected Alembic head 0039" not in text
