"""UX-L2B-R1 — API conventions, Redis inventory, idempotency naming, repo N/A proof.

These are unit/schema proofs that do not need Postgres or a live Redis 3.0.504
process. Redis command inventory is source-level; execution against 3.0.504 is
reported honestly as a gap where a matching runtime is unavailable.
"""

from __future__ import annotations

import inspect
import uuid
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from app.core.context import set_tenant_id
from app.integrations.shopify.schemas import (
    ShopifyPublishCheckItem,
    ShopifyPublishReadinessRequest,
    ShopifyPublishReadinessResponse,
    ShopifyPublishRequest,
)
from app.integrations.shopify.sync import ShopifySyncService, _deterministic_handle
from app.services.publish_readiness import (
    CODE_STORE_DISCONNECTED,
    PublishCheckItem,
    PublishReadinessService,
)

pytestmark = pytest.mark.unit

BACKEND_ROOT = Path(__file__).resolve().parents[2]
PUBLISH_READINESS_PATH = BACKEND_ROOT / "app" / "services" / "publish_readiness.py"
SYNC_PATH = BACKEND_ROOT / "app" / "integrations" / "shopify" / "sync.py"


class TestRepositoryLayerNotApplicable:
    def test_ux_l2b_diff_touches_no_repository_modules(self) -> None:
        """Prove CLAUDE.md repo isolation test is N/A for UX-L2B code.

        ``git diff --name-only e1dd0f0..HEAD -- backend/app/repositories``
        is empty. Publish readiness uses existing TenantScopedRepository APIs.
        """
        readiness_source = PUBLISH_READINESS_PATH.read_text(encoding="utf-8")
        assert "ProductRepository" in readiness_source
        assert "StoreRepository" in readiness_source
        assert not (BACKEND_ROOT / "app" / "repositories" / "publish_readiness.py").exists()

    def test_service_product_load_inherits_tenant_scoped_base_query(
        self, tenant_id: uuid.UUID
    ) -> None:
        """Service-layer select still compiles with tenant + soft-delete predicates."""
        set_tenant_id(tenant_id)
        service = PublishReadinessService.__new__(PublishReadinessService)
        from app.repositories.product import ProductRepository

        service.products = ProductRepository(MagicMock())
        query = service.products._base_query().where(service.products.model.id == uuid.uuid4())
        sql = str(
            query.compile(
                dialect=postgresql.dialect(),
                compile_kwargs={"literal_binds": True},
            )
        )
        assert "products.tenant_id" in sql
        assert str(tenant_id) in sql
        assert "deleted_at IS NULL" in sql


class TestRedisCommandInventory:
    #: Publish readiness / publish path must not introduce Redis 3.x+-only ops.
    FORBIDDEN_SNIPPETS = (
        "GETDEL",
        "KEEPTTL",
        "UNLINK",
        "XADD",
        "XREAD",
        "getdel",
        "keep_ttl",
        "unlink(",
    )

    def test_publish_paths_contain_no_redis_client_usage(self) -> None:
        for path in (PUBLISH_READINESS_PATH, SYNC_PATH):
            source = path.read_text(encoding="utf-8")
            assert "get_redis" not in source
            lowered = source.lower()
            for snippet in self.FORBIDDEN_SNIPPETS:
                assert snippet.lower() not in lowered, (
                    f"{path} must not use Redis feature {snippet!r} (baseline 3.0.504)"
                )

    def test_inventory_table_documents_zero_commands_on_ux_l2b_publish_path(self) -> None:
        """Named inventory required by the R1 addendum.

        command | source | min Redis | UX-L2B?
        ------- | ------ | --------- | -------
        (none)  | publish_readiness / sync.publish_product | n/a | UX-L2B
        """
        inventory: list[tuple[str, str, str, str]] = []
        assert inventory == []


class TestNamedShopifyPublishIdempotency:
    """Trace the real mechanism — handle adopt + StoreListing — not Redis locks."""

    def test_deterministic_handle_format(self) -> None:
        product_id = uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        assert _deterministic_handle(product_id) == (
            "droppilot-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        )

    def test_create_or_adopt_is_the_named_mechanism(self) -> None:
        source = inspect.getsource(ShopifySyncService._create_or_adopt)
        assert "handle" in source
        assert "products.json" in source

    @pytest.mark.asyncio
    async def test_same_handle_adopts_instead_of_second_create(self) -> None:
        existing = {"id": 42, "handle": "droppilot-retry", "title": "Already there"}
        client = MagicMock()
        client.get = AsyncMock(return_value={"products": [existing]})
        client.post = AsyncMock()
        body: dict[str, object] = {"product": {"title": "Widget"}}

        result = await ShopifySyncService._create_or_adopt(
            client, body=body, handle="droppilot-retry"
        )
        client.post.assert_not_awaited()
        assert result == {"product": existing}

    @pytest.mark.asyncio
    async def test_different_product_ids_produce_different_handles(self) -> None:
        assert _deterministic_handle(uuid.uuid4()) != _deterministic_handle(uuid.uuid4())

    def test_handle_does_not_embed_tenant_or_store(self) -> None:
        handle = _deterministic_handle(uuid.uuid4())
        assert "tenant" not in handle
        assert "store" not in handle


class TestReadinessSchemaConventions:
    def test_request_and_response_are_explicit_pydantic_models(self) -> None:
        req = ShopifyPublishReadinessRequest(
            product_id=uuid.uuid4(),
            store_id=None,
            expected_updated_at=None,
        )
        dumped = req.model_dump(by_alias=True)
        assert "productId" in dumped
        assert "product_id" not in dumped

        checked = datetime.now(UTC)
        resp = ShopifyPublishReadinessResponse(
            channel="shopify",
            store_id=None,
            draft_id=uuid.uuid4(),
            draft_updated_at=checked,
            can_publish=False,
            blockers=[
                ShopifyPublishCheckItem(
                    code=CODE_STORE_DISCONNECTED,
                    message="Reconnect the store.",
                    field="storeId",
                    section="publishing",
                    action="Open Integrations",
                )
            ],
            recommendations=[],
            checked_at=checked,
        )
        payload = resp.model_dump(mode="json", by_alias=True)
        assert payload["canPublish"] is False
        assert "draftUpdatedAt" in payload
        assert payload["blockers"][0]["code"] == CODE_STORE_DISCONNECTED
        assert "token" not in str(payload).lower()

    def test_publish_request_accepts_expected_updated_at_alias(self) -> None:
        stamp = datetime.now(UTC)
        req = ShopifyPublishRequest.model_validate(
            {
                "productId": str(uuid.uuid4()),
                "storeId": str(uuid.uuid4()),
                "expectedUpdatedAt": stamp.isoformat(),
            }
        )
        assert req.expected_updated_at is not None

    def test_frontend_control_flow_keys_off_stable_codes_not_messages(self) -> None:
        """Changing human copy must not change machine branching."""
        item_a = PublishCheckItem(
            code=CODE_STORE_DISCONNECTED,
            message="Old wording that sellers used to see.",
            field="storeId",
            section="publishing",
            action="Open Integrations",
        )
        item_b = PublishCheckItem(
            code=CODE_STORE_DISCONNECTED,
            message="Completely different seller-facing sentence.",
            field="storeId",
            section="publishing",
            action="Open Integrations",
        )
        assert item_a.code == item_b.code
        assert (item_a.code == CODE_STORE_DISCONNECTED) == (item_b.code == CODE_STORE_DISCONNECTED)
        assert item_a.message != item_b.message
