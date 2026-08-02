"""Product optimisation: turn a supplier product into an AI-assisted listing.

    Product → ProductOptimizationService → PromptService.test_render(execute=True)
                                              ├─ product_title_generator
                                              └─ product_description_generator
                                            → ProductVersion (new, active)
                                            → Product's cached AI fields

Reuses `PromptService.test_render` from Phase 9 stage 2 rather than
re-implementing render → call provider → record execution — it already does
exactly that. **No real AI call happens here.** `test_render` calls
`get_ai_provider(settings)`, which resolves to `StubProvider` unless a real
provider has been configured, and none has — every version this stage can
produce is synthetic, recorded as such on the underlying `PromptExecution`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.exceptions import AIError
from app.models.ai_prompt import PromptExecution, PromptExecutionStatus
from app.models.product import Product, ProductAIStatus, ProductVersion, ProductVersionSource
from app.repositories.product import ProductRepository, ProductVersionRepository
from app.services.base import BaseService
from app.services.prompt import PromptService

_TITLE_PROMPT = "product_title_generator"
_DESCRIPTION_PROMPT = "product_description_generator"

#: Supplier descriptions are free text of unbounded length and, per
#: `docs/PHASE_9_PLAN.md`'s risk table, untrusted third-party content. This
#: is not a prompt-injection defence — `PromptRenderer` does plain
#: substitution, and `StubProvider` ignores its input entirely — it is a
#: plain size bound so one oversized product cannot build an arbitrarily
#: large prompt.
_MAX_FEATURES_CHARS = 2000


class ProductOptimizationService(BaseService):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)
        self.products = ProductRepository(session)
        self.versions = ProductVersionRepository(session)
        self.prompts = PromptService(session)

    # -- Reads ----------------------------------------------------------------

    async def list_versions(self, product_id: uuid.UUID) -> list[ProductVersion]:
        """Version history for a product.

        `ProductRepository.get_by_id_or_raise` is the ownership check: a
        product belonging to another tenant is invisible and raises
        `NotFoundError`, the same 404-not-403 policy the whole platform uses.
        A second, manual ownership check on top would be redundant code with
        its own chance to disagree with the first.
        """
        product = await self.products.get_by_id_or_raise(product_id)
        return await self.versions.list_for_product(product.id)

    # -- Optimisation -----------------------------------------------------------

    async def optimize_product(
        self,
        product_id: uuid.UUID,
        *,
        tone: str = "professional",
        requested_by_user_id: uuid.UUID | None,
    ) -> tuple[Product, ProductVersion]:
        """Generate a new title and description, and activate them.

        Creates the version-1 original snapshot on first call for this
        product — lazily, not at import time, so
        `ProductImportService.import_product` needed no change at all. Every
        subsequent call adds one new `AI_GENERATED` version and activates it.

        Raises `app.ai.exceptions.AIError` if generation fails, after
        recording `Product.ai_status = FAILED` — every other AI field is left
        untouched, so a failed *attempt* never erases the last *good* result.
        """
        product = await self.products.get_by_id_or_raise(product_id)
        await self._ensure_original_snapshot(product)

        variables = self._build_variables(product, tone=tone)

        _, _, title_execution = await self.prompts.test_render(
            name=_TITLE_PROMPT,
            variables=variables,
            execute=True,
            executed_by_user_id=requested_by_user_id,
        )
        _, _, description_execution = await self.prompts.test_render(
            name=_DESCRIPTION_PROMPT,
            variables=variables,
            execute=True,
            executed_by_user_id=requested_by_user_id,
        )

        failed = _first_failure(title_execution, description_execution)
        if failed is not None:
            product.ai_status = ProductAIStatus.FAILED
            await self.flush()
            self.logger.warning(
                "product_optimization_failed",
                product_id=str(product.id),
                error_code=failed.error_code,
            )
            raise AIError(failed.error_message or "Product optimisation failed.")

        # `_first_failure` returning `None` means neither execution was
        # `None` and neither failed — `execute=True` was passed to both
        # `test_render` calls, so this is the only path left.
        if title_execution is None or description_execution is None:
            raise AIError("Product optimisation did not produce a result to record.")

        next_number = await self.versions.next_version_number(product.id)
        version = await self.versions.create(
            product_id=product.id,
            version_number=next_number,
            source=ProductVersionSource.AI_GENERATED,
            content={
                "title": title_execution.response_text,
                "description": description_execution.response_text,
            },
            active=False,
            ai_provider=title_execution.provider,
            prompt_execution_id=description_execution.id,
            created_by_user_id=requested_by_user_id,
        )
        activated = await self.versions.activate(product_id=product.id, version_id=version.id)
        self._apply_active_version(product, activated)
        await self.flush()

        self.logger.info(
            "product_optimized",
            product_id=str(product.id),
            version_number=activated.version_number,
            provider=activated.ai_provider,
        )
        return product, activated

    async def activate_version(self, product_id: uuid.UUID, version_id: uuid.UUID) -> Product:
        """Activate an existing version — a newer one, or a rollback to an
        older one. Same mechanism either way."""
        product = await self.products.get_by_id_or_raise(product_id)
        version = await self.versions.activate(product_id=product.id, version_id=version_id)
        self._apply_active_version(product, version)
        await self.flush()
        return product

    # -- Internals --------------------------------------------------------------

    async def _ensure_original_snapshot(self, product: Product) -> None:
        existing = await self.versions.list_for_product(product.id)
        if existing:
            return

        original = await self.versions.create(
            product_id=product.id,
            version_number=1,
            source=ProductVersionSource.ORIGINAL,
            content={"title": product.title, "description": product.description},
            active=True,
            ai_provider=None,
            prompt_execution_id=None,
            created_by_user_id=None,
        )
        self._apply_active_version(product, original)
        await self.flush()

    def _apply_active_version(self, product: Product, version: ProductVersion) -> None:
        """Sync the product's cached fields to the version just activated.

        The only place any of these five fields is assigned, anywhere in
        this stage — `Product.title`/`Product.description` are never touched
        here or by anything else, which is the actual guarantee behind "AI
        content cannot overwrite supplier source", not just a description of
        intent.
        """
        product.ai_version = version.version_number

        if version.source is ProductVersionSource.ORIGINAL:
            product.ai_status = ProductAIStatus.NOT_OPTIMIZED
            product.ai_provider = None
            product.ai_last_generated_at = None
            product.optimized_title = None
            product.optimized_description = None
            return

        product.ai_status = ProductAIStatus.OPTIMIZED
        product.ai_provider = version.ai_provider
        product.ai_last_generated_at = datetime.now(UTC)
        product.optimized_title = version.content.get("title")
        product.optimized_description = version.content.get("description")

    @staticmethod
    def _build_variables(product: Product, *, tone: str) -> dict[str, str]:
        features = (product.description or "")[:_MAX_FEATURES_CHARS]
        return {
            "product_title": product.title,
            "category": product.category_name or "",
            "brand": product.brand or "",
            "features": features,
            "tone": tone,
        }


def _first_failure(
    *executions: PromptExecution | None,
) -> PromptExecution | None:
    """Return the first execution whose status is `FAILED`, if any."""
    for execution in executions:
        if execution is not None and execution.status is PromptExecutionStatus.FAILED:
            return execution
    return None


__all__ = ["ProductOptimizationService"]
