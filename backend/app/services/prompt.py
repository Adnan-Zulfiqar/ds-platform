"""Prompt management: create, version, activate, and test-render prompts.

**No real AI call lives here.** ``test_render`` with ``execute=True`` calls
``get_ai_provider(settings)``, which Stage 1 wires to return ``StubProvider``
unless a real provider has been configured — and none has, so every execution
this stage can produce is synthetic and marked as such. This exists to prove
the render → call → record plumbing end-to-end, not to prove generation
quality; see ``docs/PHASE_9_PLAN.md`` Stage 2 notes.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.exceptions import AIError
from app.ai.factory import get_ai_provider
from app.ai.prompt_renderer import PromptRenderer
from app.ai.provider import CompletionRequest, ImageAnalysisRequest, ImageAnalysisResult
from app.core.config import settings
from app.core.exceptions import ConflictError, NotFoundError, PermissionDeniedError
from app.models.ai_prompt import AIPrompt, PromptExecution, PromptExecutionStatus
from app.repositories.ai_prompt import PromptExecutionRepository, PromptRepository
from app.schemas.common import ListQueryParams
from app.services.base import BaseService


class PromptService(BaseService):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)
        self.prompts = PromptRepository(session)
        self.executions = PromptExecutionRepository(session)

    @staticmethod
    def _require_prompt_mutation_allowed() -> None:
        """Refuse mutating platform-global prompts unless explicitly enabled.

        ``ai_prompts`` is shared across every tenant. A workspace admin editing
        ``product_title_generator`` would change every other tenant's output —
        audit A-03. Reads and test-render remain available; only create /
        version / activate are gated.
        """
        if not settings.ai.allow_prompt_mutation:
            raise PermissionDeniedError(
                "Editing platform AI prompts is disabled. "
                "Set AI_ALLOW_PROMPT_MUTATION=true only for platform operators."
            )

    # -- Creation and versioning ---------------------------------------------

    async def create_prompt(
        self,
        *,
        name: str,
        description: str | None,
        template: str,
        target_model: str | None,
        created_by_user_id: uuid.UUID | None,
    ) -> AIPrompt:
        """Create a new prompt name at version 1, active immediately.

        Rejects a name that already has at least one version — use
        :meth:`create_version` to add to an existing prompt. The uniqueness
        check here is a friendly early error; the partial unique index on
        ``ai_prompts`` is what actually prevents two concurrent callers from
        both winning a race to create the same new name, by rejecting the
        second INSERT outright.
        """
        self._require_prompt_mutation_allowed()
        existing = await self.prompts.list_versions(name)
        if existing:
            raise ConflictError(f"A prompt named {name!r} already exists.")

        return await self.prompts.create(
            name=name,
            description=description,
            template=template,
            target_model=target_model,
            version=1,
            active=True,
            created_by_user_id=created_by_user_id,
        )

    async def create_version(
        self,
        *,
        name: str,
        template: str,
        description: str | None,
        target_model: str | None,
        created_by_user_id: uuid.UUID | None,
    ) -> AIPrompt:
        """Add a new, inactive version under an existing name.

        Never mutates a stored template — this always inserts a new row.
        Activate it explicitly with :meth:`activate_version` when it is
        ready; until then the previous version keeps serving.
        """
        self._require_prompt_mutation_allowed()
        versions = await self.prompts.list_versions(name)
        if not versions:
            raise NotFoundError.for_resource("Prompt", name)

        latest = versions[0]
        next_version = await self.prompts.next_version_number(name)
        return await self.prompts.create(
            name=name,
            description=description if description is not None else latest.description,
            template=template,
            target_model=target_model if target_model is not None else latest.target_model,
            version=next_version,
            active=False,
            created_by_user_id=created_by_user_id,
        )

    async def activate_version(self, *, name: str, version: int) -> AIPrompt:
        """Make ``version`` the active row for ``name``.

        The same operation serves both "activate a newly-created version"
        and "roll back to an older one" — there is no separate rollback
        endpoint because there is no separate mechanism.
        """
        self._require_prompt_mutation_allowed()
        return await self.prompts.activate(name=name, version=version)

    # -- Reads ----------------------------------------------------------------

    async def get_active(self, name: str) -> AIPrompt:
        return await self.prompts.get_active_or_raise(name)

    async def list_history(self, name: str) -> Sequence[AIPrompt]:
        versions = await self.prompts.list_versions(name)
        if not versions:
            raise NotFoundError.for_resource("Prompt", name)
        return versions

    async def list_active_prompts(self, params: ListQueryParams) -> tuple[Sequence[AIPrompt], int]:
        return await self.prompts.list_active(params)

    # -- Test rendering ---------------------------------------------------------

    async def test_render(
        self,
        *,
        name: str,
        variables: dict[str, str],
        execute: bool,
        executed_by_user_id: uuid.UUID | None,
    ) -> tuple[str, list[str], PromptExecution | None]:
        """Render the active version of ``name``, optionally running it.

        Returns the rendered text, the template's required variable names
        (so a caller does not need a second request to learn what it needs
        to supply), and the recorded execution when ``execute`` is true.

        Rendering failures (a missing variable) propagate directly — there is
        nothing meaningful to record for a request that never produced text.
        Only a call that actually reached a provider, successfully or not,
        writes a :class:`PromptExecution` row.
        """
        prompt = await self.get_active(name)
        required = sorted(PromptRenderer.extract_variables(prompt.template))
        rendered = PromptRenderer.render(prompt.template, variables)

        if not execute:
            return rendered, required, None

        # Track E6b: every AI feature reaches the provider through here.
        from app.services.entitlements import BillingGate

        await BillingGate(self.session).require_ai()
        started = time.monotonic()
        try:
            provider = get_ai_provider(settings)
            result = await provider.complete(CompletionRequest(prompt=rendered))
        except AIError as exc:
            duration_ms = int((time.monotonic() - started) * 1000)
            execution = await self.executions.create(
                prompt_id=prompt.id,
                prompt_name=prompt.name,
                prompt_version=prompt.version,
                input_variables=dict(variables),
                rendered_prompt=rendered,
                provider=settings.ai.provider.value,
                model=None,
                response_text=None,
                is_synthetic=False,
                status=PromptExecutionStatus.FAILED,
                error_code=type(exc).__name__,
                error_message=str(exc)[:2048],
                duration_ms=duration_ms,
                executed_by_user_id=executed_by_user_id,
            )
            return rendered, required, execution

        duration_ms = int((time.monotonic() - started) * 1000)
        execution = await self.executions.create(
            prompt_id=prompt.id,
            prompt_name=prompt.name,
            prompt_version=prompt.version,
            input_variables=dict(variables),
            rendered_prompt=rendered,
            provider=result.provider,
            model=result.model,
            response_text=result.text,
            is_synthetic=result.is_synthetic,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            status=PromptExecutionStatus.SUCCEEDED,
            duration_ms=duration_ms,
            executed_by_user_id=executed_by_user_id,
        )
        return rendered, required, execution

    async def execute_image_analysis(
        self,
        *,
        name: str,
        variables: dict[str, str],
        executed_by_user_id: uuid.UUID | None,
    ) -> tuple[str, ImageAnalysisResult | None, PromptExecution]:
        """Render ``name`` and call ``analyse_image``, never ``complete``.

        Stage 6 always passes ``image_analyzer``. A missing template variable
        still raises before any execution row is written. ``AIError`` is an
        expected domain failure: a FAILED row is recorded and the result is
        ``None``. Any other exception propagates without a row.
        """
        prompt = await self.get_active(name)
        rendered = PromptRenderer.render(prompt.template, variables)

        # Track E6b: every AI feature reaches the provider through here.
        from app.services.entitlements import BillingGate

        await BillingGate(self.session).require_ai()
        started = time.monotonic()
        try:
            provider = get_ai_provider(settings)
            result = await provider.analyse_image(
                ImageAnalysisRequest(
                    image_url=variables["image_url"],
                    instructions=rendered,
                )
            )
        except AIError as exc:
            duration_ms = int((time.monotonic() - started) * 1000)
            execution = await self.executions.create(
                prompt_id=prompt.id,
                prompt_name=prompt.name,
                prompt_version=prompt.version,
                input_variables=dict(variables),
                rendered_prompt=rendered,
                provider=settings.ai.provider.value,
                model=None,
                response_text=None,
                is_synthetic=False,
                status=PromptExecutionStatus.FAILED,
                error_code=type(exc).__name__,
                error_message=str(exc)[:2048],
                duration_ms=duration_ms,
                executed_by_user_id=executed_by_user_id,
            )
            return rendered, None, execution

        duration_ms = int((time.monotonic() - started) * 1000)
        response_text = json.dumps(
            {"altText": result.alt_text, "caption": result.caption},
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        execution = await self.executions.create(
            prompt_id=prompt.id,
            prompt_name=prompt.name,
            prompt_version=prompt.version,
            input_variables=dict(variables),
            rendered_prompt=rendered,
            provider=result.provider,
            model=result.model,
            response_text=response_text,
            is_synthetic=result.is_synthetic,
            input_tokens=None,
            output_tokens=None,
            status=PromptExecutionStatus.SUCCEEDED,
            duration_ms=duration_ms,
            executed_by_user_id=executed_by_user_id,
        )
        return rendered, result, execution


__all__ = ["PromptService"]
