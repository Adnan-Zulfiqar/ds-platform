"""User endpoints.

These two read endpoints are the reference implementation of the request
pipeline: dependency injection resolves the session and the tenant, the
repository applies tenant scoping and pagination, and a schema controls exactly
what is serialised.

Note what the handlers do *not* contain: no SQL, no tenant filtering, no
transaction management, no error translation. Each is roughly three lines —
validate, delegate, return — which is the shape every handler in this codebase
should keep.

Write endpoints are absent because creating a user requires password hashing and
an invitation flow, which belong to the auth phase.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path

from app.api.deps import UserRepo
from app.schemas.common import ListQueryParams, Page, list_query_params
from app.schemas.user import UserRead

router = APIRouter(prefix="/users", tags=["users"])


@router.get(
    "",
    response_model=Page[UserRead],
    summary="List users in the current tenant",
)
async def list_users(
    repository: UserRepo,
    params: Annotated[ListQueryParams, Depends(list_query_params)],
) -> Page[UserRead]:
    """Return a page of users belonging to the current tenant.

    The tenant filter is not written here — it is applied inside
    ``TenantScopedRepository``, so this endpoint cannot accidentally return
    another tenant's users even if the handler is edited carelessly.
    """
    users, total = await repository.list(params)
    return Page[UserRead].build(
        items=[UserRead.model_validate(user) for user in users],
        page=params.page,
        size=params.size,
        total_items=total,
    )


@router.get(
    "/{user_id}",
    response_model=UserRead,
    summary="Fetch a single user",
)
async def get_user(
    repository: UserRepo,
    user_id: Annotated[uuid.UUID, Path(description="Identifier of the user to fetch.")],
) -> UserRead:
    """Return one user by id.

    A user belonging to a different tenant is reported as 404 rather than 403,
    so that identifiers cannot be probed for existence across tenants.
    """
    user = await repository.get_by_id_or_raise(user_id)
    return UserRead.model_validate(user)
