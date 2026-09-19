"""Stage 7 boundaries: no API, no extra repository, no import cycle, no schema leak."""

from __future__ import annotations

import ast
import uuid
from pathlib import Path

import pytest

from app.api.v1.router import api_router
from app.schemas.product import ProductImageRead
from app.services.image_analysis import ImageAnalysisItem, ImageAnalysisReport
from app.services.product_pipeline import PipelinePreview, _pipeline_warnings

pytestmark = pytest.mark.unit

BACKEND_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = Path(__file__).resolve().parents[3]


def test_product_optimization_does_not_import_product_pipeline() -> None:
    source = (BACKEND_ROOT / "app/services/product_optimization.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
            imported.update(alias.name for alias in node.names)
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
    assert "app.services.product_pipeline" not in imported
    assert "product_pipeline" not in source


def test_product_version_repository_module_does_not_exist() -> None:
    assert not (BACKEND_ROOT / "app/repositories/product_version.py").exists()


def test_stage_7_adds_no_api_router() -> None:
    paths = [getattr(route, "path", "") for route in api_router.routes]
    joined = " ".join(paths)
    assert "/pipeline" not in joined
    assert "product-pipeline" not in joined
    assert not (BACKEND_ROOT / "app/api/v1/product_pipeline").exists()
    assert not (BACKEND_ROOT / "app/api/v1/product_pipeline.py").exists()
    assert not (BACKEND_ROOT / "app/api/v1/pipeline.py").exists()


def test_product_image_read_still_has_no_analysis() -> None:
    assert "analysis" not in ProductImageRead.model_fields


def test_pipeline_preview_does_not_claim_a_store_listing() -> None:
    assert "listing_id" not in PipelinePreview.__dataclass_fields__
    assert "store_listing_id" not in PipelinePreview.__dataclass_fields__
    assert "published_version_id" not in PipelinePreview.__dataclass_fields__


def test_optimization_quality_does_not_import_app_ai() -> None:
    source = (BACKEND_ROOT / "app/services/optimization_quality.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("app.ai"):
            raise AssertionError(f"optimization_quality imports {node.module}")
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("app.ai")


def test_no_stage_7_celery_task() -> None:
    tasks = BACKEND_ROOT / "app/tasks"
    if not tasks.exists():
        return
    for path in tasks.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "ProductPipelineService" not in text
        assert "generate_candidate" not in text or "product_pipeline" not in text


def test_frontend_has_no_pipeline_preview_type() -> None:
    text = (REPO_ROOT / "frontend" / "types" / "api.ts").read_text(encoding="utf-8")
    assert "PipelinePreview" not in text
    assert "pipelineCandidateVersion" not in text


class TestImageAnalysisWarnings:
    def test_expected_failures_are_warnings_not_blockers(self) -> None:
        report = ImageAnalysisReport(
            product_id=uuid.uuid4(),
            images=(
                ImageAnalysisItem(
                    image_id=uuid.uuid4(),
                    position=0,
                    status="fetchFailed",
                    error_code="fetchFailed",
                    analysis={},
                ),
                ImageAnalysisItem(
                    image_id=uuid.uuid4(),
                    position=1,
                    status="decodeFailed",
                    error_code="decodeFailed",
                    analysis={},
                ),
                ImageAnalysisItem(
                    image_id=uuid.uuid4(),
                    position=2,
                    status="checksOnly",
                    error_code=None,
                    analysis={
                        "checks": {
                            "blur": {"isBlurry": True},
                            "duplicates": {"duplicateOfImageIds": ["x"]},
                        }
                    },
                ),
            ),
        )
        warnings = _pipeline_warnings(report)
        codes = [item.code for item in warnings]
        assert "image_analysis_incomplete" in codes
        assert "image_analysis_checks_only" in codes
        assert "image_blurry" in codes
        assert "image_duplicate" in codes
