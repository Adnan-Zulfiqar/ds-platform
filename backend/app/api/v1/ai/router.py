"""AI prompt management endpoints.

Thin, like every router here: validate, delegate, return. All seven endpoints
require `RequireAdmin`, including reads — see the "known limitation" note in
`docs/PHASE_9_PLAN.md` Stage 2 notes for why this is stricter than most list
endpoints in the platform and what it does not yet solve.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, status

from app.ai.prompt_renderer import PromptRenderer
from app.api.deps import DbSession, RequireAdmin
from app.models.ai_prompt import AIPrompt
from app.schemas.ai_prompt import (
    AIPromptRead,
    PromptCreateRequest,
    PromptExecutionRead,
    PromptTestRenderRequest,
    PromptTestRenderResponse,
    PromptVersionCreateRequest,
)
from app.schemas.common import ListQueryParams, Page, list_query_params
from app.services.prompt import PromptService

router = APIRouter(prefix="/ai/prompts", tags=["ai-prompts"])

_NamePath = Annotated[str, Path(min_length=1, max_length=128)]


def _to_read(prompt: AIPrompt) -> AIPromptRead:
    """Project a prompt into its response schema.

    ``required_variables`` is computed here rather than read from a column —
    there is no column; see ``AIPrompt``'s docstring.
    """
    return AIPromptRead(
        id=prompt.id,
        name=prompt.name,
        description=prompt.description,
        template=prompt.template,
        target_model=prompt.target_model,
        version=prompt.version,
        active=prompt.active,
        required_variables=sorted(PromptRenderer.extract_variables(prompt.template)),
        created_at=prompt.created_at,
        updated_at=prompt.updated_at,
    )


@router.get(
    "",
    response_model=Page[AIPromptRead],
    summary="List prompts (the active version of each)",
)
async def list_prompts(
    session: DbSession,
    params: Annotated[ListQueryParams, Depends(list_query_params)],
    _authorized: RequireAdmin,
) -> Page[AIPromptRead]:
    prompts, total = await PromptService(session).list_active_prompts(params)
    return Page[AIPromptRead].build(
        items=[_to_read(p) for p in prompts],
        page=params.page,
        size=params.size,
        total_items=total,
    )


@router.post(
    "",
    response_model=AIPromptRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new prompt at version 1",
)
async def create_prompt(
    body: PromptCreateRequest,
    session: DbSession,
    principal: RequireAdmin,
) -> AIPromptRead:
    prompt = await PromptService(session).create_prompt(
        name=body.name,
        description=body.description,
        template=body.template,
        target_model=body.target_model,
        created_by_user_id=principal.user_id,
    )
    return _to_read(prompt)


@router.get(
    "/{name}",
    response_model=AIPromptRead,
    summary="Get the active version of a prompt",
)
async def get_active_prompt(
    name: _NamePath,
    session: DbSession,
    _authorized: RequireAdmin,
) -> AIPromptRead:
    prompt = await PromptService(session).get_active(name)
    return _to_read(prompt)


@router.get(
    "/{name}/history",
    response_model=Page[AIPromptRead],
    summary="Version history for a prompt, newest first",
)
async def get_prompt_history(
    name: _NamePath,
    session: DbSession,
    params: Annotated[ListQueryParams, Depends(list_query_params)],
    _authorized: RequireAdmin,
) -> Page[AIPromptRead]:
    versions = await PromptService(session).list_history(name)
    total = len(versions)
    page_items = versions[params.offset : params.offset + params.limit]
    return Page[AIPromptRead].build(
        items=[_to_read(v) for v in page_items],
        page=params.page,
        size=params.size,
        total_items=total,
    )


@router.post(
    "/{name}/versions",
    response_model=AIPromptRead,
    status_code=status.HTTP_201_CREATED,
    summary="Add a new, inactive version",
)
async def create_prompt_version(
    name: _NamePath,
    body: PromptVersionCreateRequest,
    session: DbSession,
    principal: RequireAdmin,
) -> AIPromptRead:
    prompt = await PromptService(session).create_version(
        name=name,
        template=body.template,
        description=body.description,
        target_model=body.target_model,
        created_by_user_id=principal.user_id,
    )
    return _to_read(prompt)


@router.post(
    "/{name}/versions/{version}/activate",
    response_model=AIPromptRead,
    summary="Activate a version — also how rollback works",
)
async def activate_prompt_version(
    name: _NamePath,
    version: Annotated[int, Path(ge=1)],
    session: DbSession,
    _authorized: RequireAdmin,
) -> AIPromptRead:
    prompt = await PromptService(session).activate_version(name=name, version=version)
    return _to_read(prompt)


@router.post(
    "/{name}/test",
    response_model=PromptTestRenderResponse,
    summary="Render the active version, optionally running it",
)
async def test_prompt_render(
    name: _NamePath,
    body: PromptTestRenderRequest,
    session: DbSession,
    principal: RequireAdmin,
) -> PromptTestRenderResponse:
    rendered, required, execution = await PromptService(session).test_render(
        name=name,
        variables=body.variables,
        execute=body.execute,
        executed_by_user_id=principal.user_id,
    )
    return PromptTestRenderResponse(
        rendered_prompt=rendered,
        required_variables=required,
        execution=(
            PromptExecutionRead.model_validate(execution) if execution is not None else None
        ),
    )


__all__ = ["router"]
