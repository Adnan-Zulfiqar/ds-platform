"""Product optimisation: turn a supplier product into an AI-assisted listing.

    Product → ProductOptimizationService → PromptService.test_render(execute=True)
                                              ├─ product_title_generator
                                              ├─ product_description_generator
                                              └─ seo_optimizer
                                            → score_version (stage 5, pure)
                                            → ProductVersion (new)
                                            → Product's cached AI fields (legacy
                                              optimize / pipeline approve only)

Reuses `PromptService.test_render` from Phase 9 stage 2 rather than
re-implementing render → call provider → record execution — it already does
exactly that. **No real AI call happens here.** `test_render` calls
`get_ai_provider(settings)`, which resolves to `StubProvider` unless a real
provider has been configured, and none has — every version this stage can
produce is synthetic, recorded as such on the underlying `PromptExecution`.

Stage 7 splits generation from activation. `_generate_version` is the shared
core; `optimize_product` still auto-activates an unmarked row; `generate_candidate`
writes an inactive pipeline-marked row. Pipeline metadata lives here so
`activate_version` can refuse a preview without importing the pipeline service.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.exceptions import AIError
from app.core.exceptions import NotFoundError, ValidationError
from app.core.sanitize import html_to_plain_text
from app.models.ai_prompt import PromptExecution, PromptExecutionStatus
from app.models.product import Product, ProductAIStatus, ProductVersion, ProductVersionSource
from app.repositories.product import ProductRepository, ProductVersionRepository
from app.services.base import BaseService
from app.services.optimization_quality import score_version
from app.services.prompt import PromptService

_TITLE_PROMPT = "product_title_generator"
_DESCRIPTION_PROMPT = "product_description_generator"
_SEO_PROMPT = "seo_optimizer"

#: Supplier descriptions are free text of unbounded length and, per
#: `docs/PHASE_9_PLAN.md`'s risk table, untrusted third-party content. This
#: is not a prompt-injection defence — `PromptRenderer` does plain
#: substitution, and `StubProvider` ignores its input entirely — it is a
#: plain size bound so one oversized product cannot build an arbitrarily
#: large prompt.
_MAX_FEATURES_CHARS = 2000

_PIPELINE_METADATA_KEYS = (
    "pipelineCandidateVersion",
    "pipelineSourceUpdatedAt",
    "isSynthetic",
)
_REASON_NOT_A_PIPELINE_CANDIDATE = "not_a_pipeline_candidate"
_REASON_PIPELINE_REQUIRES_APPROVAL = "pipeline_candidate_requires_approval"


@dataclass(frozen=True, slots=True)
class PipelineCandidateMetadata:
    source_updated_at: datetime
    is_synthetic: bool


def content_has_any_pipeline_metadata_key(content: object) -> bool:
    """True when any pipeline key is present, even if the values are garbage.

    `activate_version` uses this to split unmarked legacy/ORIGINAL rows from
    a corrupt pipeline object. Type checking belongs in
    `parse_pipeline_candidate_metadata`, not here — a bool `True` marker
    must still be treated as a pipeline row so it cannot be activated as
    if it were a legacy optimize version (`True == 1` in Python).
    """
    if not isinstance(content, dict):
        return False
    return any(key in content for key in _PIPELINE_METADATA_KEYS)


def parse_pipeline_candidate_metadata(content: object) -> PipelineCandidateMetadata:
    """Fail closed on anything that is not an exact Stage 7 pipeline candidate.

    Channel publication and approval both depend on this. A missing flag
    must not become "real AI", and `True == 1` must not become marker
    version 1.
    """
    if not isinstance(content, dict):
        raise _not_a_pipeline_candidate()

    marker = content.get("pipelineCandidateVersion")
    if type(marker) is not int or marker != 1:
        raise _not_a_pipeline_candidate()

    raw_source = content.get("pipelineSourceUpdatedAt")
    if type(raw_source) is not str:
        raise _not_a_pipeline_candidate()
    try:
        source_updated_at = datetime.fromisoformat(raw_source)
    except ValueError as exc:
        raise _not_a_pipeline_candidate() from exc
    if source_updated_at.tzinfo is None:
        raise _not_a_pipeline_candidate()

    is_synthetic = content.get("isSynthetic")
    if type(is_synthetic) is not bool:
        raise _not_a_pipeline_candidate()

    return PipelineCandidateMetadata(
        source_updated_at=source_updated_at,
        is_synthetic=is_synthetic,
    )


def _not_a_pipeline_candidate() -> ValidationError:
    return ValidationError(
        "This version is not a pipeline candidate.",
        details={"reason": _REASON_NOT_A_PIPELINE_CANDIDATE},
    )


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

    # -- Generation ------------------------------------------------------------

    async def generate_candidate(
        self,
        product_id: uuid.UUID,
        *,
        tone: str = "professional",
        requested_by_user_id: uuid.UUID | None,
    ) -> ProductVersion:
        """Create an inactive pipeline candidate. Does not activate or publish.

        Expected generation failure must not flip `Product.ai_status` to
        FAILED — that status is the legacy optimize contract, and a preview
        retry must not look like an optimize failure.
        """
        _product, version = await self._generate_version(
            product_id,
            tone=tone,
            requested_by_user_id=requested_by_user_id,
            as_pipeline_candidate=True,
        )
        return version

    async def optimize_product(
        self,
        product_id: uuid.UUID,
        *,
        tone: str = "professional",
        requested_by_user_id: uuid.UUID | None,
    ) -> tuple[Product, ProductVersion]:
        """Generate a new title, description, and SEO proposal, and activate
        the version that holds them.

        Creates the version-1 original snapshot on first call for this
        product — lazily, not at import time, so
        `ProductImportService.import_product` needed no change at all. Every
        subsequent call adds one new unmarked `AI_GENERATED` version and
        activates it.

        Three generations, one outcome: if any of them fails, no version is
        written at all. Raises `app.ai.exceptions.AIError` after recording
        `Product.ai_status = FAILED` — every other AI field is left
        untouched, so a failed *attempt* never erases the last *good* result,
        and a half-generated result (title and description succeeded, SEO
        did not) is never persisted as if it were whole.
        """
        try:
            product, version = await self._generate_version(
                product_id,
                tone=tone,
                requested_by_user_id=requested_by_user_id,
                as_pipeline_candidate=False,
            )
        except AIError:
            product = await self.products.get_by_id_or_raise(product_id)
            product.ai_status = ProductAIStatus.FAILED
            await self.flush()
            raise
        # Generation stays outside FOR UPDATE so the three prompt calls do not
        # hold the Product row. Activation then follows the same Product →
        # ProductVersion → cache order as pipeline approve; otherwise a
        # concurrent approve and this path deadlock on crossed row locks.
        locked = await self.products.lock_for_update(product.id)
        if locked is None:
            raise NotFoundError.for_resource("Product", product.id)
        activated = await self.versions.activate(product_id=locked.id, version_id=version.id)
        self._apply_active_version(locked, activated)
        await self.flush()
        product = locked

        self.logger.info(
            "product_optimized",
            product_id=str(product.id),
            version_number=activated.version_number,
            provider=activated.ai_provider,
        )
        return product, activated

    async def activate_version(self, product_id: uuid.UUID, version_id: uuid.UUID) -> Product:
        """Activate ORIGINAL or legacy AI versions.

        Pipeline candidates must go through `ProductPipelineService.approve`.
        Product `FOR UPDATE` is taken first so this path cannot deadlock
        with pipeline approve (Product then ProductVersion). Parsing lives
        in this module so this method never imports the pipeline service.
        """
        product = await self.products.lock_for_update(product_id)
        if product is None:
            raise NotFoundError.for_resource("Product", product_id)
        version = await self.versions.get_by_id_for_product(
            product_id=product.id,
            version_id=version_id,
            populate_existing=True,
        )
        if version is None:
            raise NotFoundError.for_resource("ProductVersion", version_id)
        if content_has_any_pipeline_metadata_key(version.content):
            parse_pipeline_candidate_metadata(version.content)
            raise ValidationError(
                "This version is a pipeline preview and must be approved "
                "through the product pipeline.",
                details={"reason": _REASON_PIPELINE_REQUIRES_APPROVAL},
            )
        activated = await self.versions.activate(product_id=product.id, version_id=version_id)
        self._apply_active_version(product, activated)
        await self.flush()
        return product

    async def _generate_version(
        self,
        product_id: uuid.UUID,
        *,
        tone: str,
        requested_by_user_id: uuid.UUID | None,
        as_pipeline_candidate: bool,
    ) -> tuple[Product, ProductVersion]:
        """Shared prompt/score/write path. Does not activate.

        Pipeline rows are marked here, not by wrapping `optimize_product`.
        A single public generator would let the legacy HTTP shortcut mint a
        pipeline candidate and auto-activate it, skipping approve.
        """
        product = await self.products.get_by_id_or_raise(product_id)
        original = await self._ensure_original_snapshot(product)
        await self.session.refresh(product)
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
        _, _, seo_execution = await self.prompts.test_render(
            name=_SEO_PROMPT,
            variables=variables,
            execute=True,
            executed_by_user_id=requested_by_user_id,
        )

        failed = _first_failure(title_execution, description_execution, seo_execution)
        if failed is not None:
            self.logger.warning(
                "product_optimization_failed",
                product_id=str(product.id),
                error_code=failed.error_code,
                as_pipeline_candidate=as_pipeline_candidate,
            )
            raise AIError(failed.error_message or "Product optimisation failed.")

        # `_first_failure` returning `None` means no execution was `None`
        # and none failed — `execute=True` was passed to all three
        # `test_render` calls, so this is the only path left.
        if title_execution is None or description_execution is None or seo_execution is None:
            raise AIError("Product optimisation did not produce a result to record.")

        # The SEO keys hold the `seo_optimizer` completion verbatim. The
        # seeded template asks for a title, a meta description, and keywords
        # in one response, and no provider yet returns anything parseable —
        # `StubProvider` returns one opaque synthetic string. Splitting that
        # into three "fields" would be fabricated model output. The stage
        # that ships a real provider defines the parse contract; until then
        # all three values are the same raw text, and say so by being
        # `[STUB-AI]`-prefixed. See docs/PHASE_9_STAGE_4_PLAN.md §3.
        generated = {
            "title": title_execution.response_text,
            "description": description_execution.response_text,
            "seoTitle": seo_execution.response_text,
            "seoDescription": seo_execution.response_text,
            "keywords": seo_execution.response_text,
        }

        # Stage 5: score the candidate and the original with the same
        # merchant keywords at the same moment, before anything is written.
        # The baseline is recomputed from the original's immutable content,
        # never read from a number stored on it — a stored number could
        # carry an older rubric version, or (legacy rows) not exist at all.
        # A scorer exception here is a bug, not a generation failure: it
        # propagates before `create`, so no version is persisted and
        # `ai_status` is left alone. See docs/PHASE_9_STAGE_5_PLAN.md §11.
        scored = score_version(generated, product)
        baseline = score_version(original.content, product)
        content: dict[str, Any] = {
            **generated,
            **scored.as_content(baseline=baseline),
        }
        if as_pipeline_candidate:
            content["pipelineCandidateVersion"] = 1
            content["pipelineSourceUpdatedAt"] = product.updated_at.isoformat()
            content["isSynthetic"] = bool(
                title_execution.is_synthetic
                or description_execution.is_synthetic
                or seo_execution.is_synthetic
            )

        next_number = await self.versions.next_version_number(product.id)
        version = await self.versions.create(
            product_id=product.id,
            version_number=next_number,
            source=ProductVersionSource.AI_GENERATED,
            content=content,
            active=False,
            ai_provider=title_execution.provider,
            prompt_execution_id=description_execution.id,
            created_by_user_id=requested_by_user_id,
        )
        return product, version

    # -- Internals --------------------------------------------------------------

    async def _ensure_original_snapshot(self, product: Product) -> ProductVersion:
        """Return the version-1 original snapshot, creating it on first call.

        Returned rather than discarded because stage 5 scores every AI
        version against the original's immutable content. `list_for_product`
        is newest-first, so version 1 is the last element.
        """
        existing = await self.versions.list_for_product(product.id)
        if existing:
            return existing[-1]

        # Stage 5 scores the snapshot at creation. `qualityBaseline` /
        # `qualityDelta` are not written on the original: it is the baseline.
        snapshot = {"title": product.title, "description": product.description}
        original = await self.versions.create(
            product_id=product.id,
            version_number=1,
            source=ProductVersionSource.ORIGINAL,
            content={**snapshot, **score_version(snapshot, product).as_content()},
            active=True,
            ai_provider=None,
            prompt_execution_id=None,
            created_by_user_id=None,
        )
        self._apply_active_version(product, original)
        await self.flush()
        return original

    def _apply_active_version(self, product: Product, version: ProductVersion) -> None:
        """Sync the product's cached fields to the version just activated.

        The only place any of these five fields is assigned, anywhere in
        this stage — `Product.title`/`Product.description` are never touched
        here or by anything else, which is the actual guarantee behind "AI
        content cannot overwrite supplier source", not just a description of
        intent.

        Deliberately reads only `title` and `description` out of `content`.
        The SEO keys Stage 4 added (`seoTitle`, `seoDescription`,
        `keywords`) stay in the version row: `Product.seo_title`,
        `seo_description`, `meta_keywords`, and `tags` are the merchant's,
        and activating a version — or rolling one back — must not overwrite
        them with a proposal the merchant never accepted. The stage 5
        quality keys likewise stay on the version: activation neither
        recomputes nor copies a score anywhere.
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
        # `description` is sanitized HTML (Product Editor stage 1) — reduced
        # to plain text here rather than fed to the prompt with markup still
        # in it, which would put literal `<p>`/`<img>` tags into model input.
        features = html_to_plain_text(product.description)[:_MAX_FEATURES_CHARS]
        return {
            "product_title": product.title,
            "category": product.category_name or "",
            "brand": product.brand or "",
            "features": features,
            "tone": tone,
            "keywords": _keywords_for_prompt(product),
        }


def _keywords_for_prompt(product: Product) -> str:
    """The `{{keywords}}` value for `seo_optimizer`, from what the merchant
    already recorded — never generated here.

    First non-empty source wins: `search_topics` (the planning-topics list
    Stage 3 added for exactly this use), then `tags`, then the legacy
    `meta_keywords` free text. Lists are joined the way
    `integrations/shopify/sync.py` already joins `tags`. The result is
    always a string, so `keywords` is never a *missing* variable —
    `MissingPromptVariablesError` stays reserved for a template whose
    variable genuinely was not supplied.
    """
    for values in (product.search_topics, product.tags):
        cleaned = [value.strip() for value in values or [] if value and value.strip()]
        if cleaned:
            return ", ".join(cleaned)
    return (product.meta_keywords or "").strip()


def _first_failure(
    *executions: PromptExecution | None,
) -> PromptExecution | None:
    """Return the first execution whose status is `FAILED`, if any."""
    for execution in executions:
        if execution is not None and execution.status is PromptExecutionStatus.FAILED:
            return execution
    return None


__all__ = [
    "PipelineCandidateMetadata",
    "ProductOptimizationService",
    "content_has_any_pipeline_metadata_key",
    "parse_pipeline_candidate_metadata",
]
