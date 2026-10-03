"""User endpoints.

These two read endpoints are the reference implementation of the request
pipeline: dependency injection resolves the session and the tenant, the
repository applies tenant scoping and pagination, and a schema controls exactly
what is serialised.

Note what the handlers do *not* contain: no SQL, no tenant filtering, no
transaction management, no error translation. Each is roughly three lines —
validate, delegate, return — which is the shape every handler in this codebase
should keep.

Users are created by registration (the owner) and by accepting a team
invitation (Track E4). The invitation endpoints live here because they manage
the roster; accepting one lives in the auth router, beside registration,
because it signs the new user in.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path, status

from app.api.deps import (
    CurrentUser,
    DbSession,
    RequireAdmin,
    RequireViewer,
    UserRepo,
    endpoint_rate_limit,
)
from app.models.role import RoleName
from app.schemas.common import ListQueryParams, Page, list_query_params
from app.schemas.invitation import InvitationCreate, InvitationRead
from app.schemas.user import UserRead
from app.services.team_invitations import TeamInvitationService

router = APIRouter(prefix="/users", tags=["users"])

_invite_limit = endpoint_rate_limit("team-invite", limit=30, window_seconds=3600)


@router.get(
    "",
    response_model=Page[UserRead],
    summary="List users in the current tenant",
)
async def list_users(
    repository: UserRepo,
    params: Annotated[ListQueryParams, Depends(list_query_params)],
    _authorized: RequireViewer,
) -> Page[UserRead]:
    """Return a page of users belonging to the current tenant.

    Two independent protections apply, and neither is written in this function:

    * **Authorization** — ``RequireViewer`` runs as a dependency, before the
      handler body. Reading the team roster is appropriate for every real role,
      so the floor is `viewer`; the check still rejects a token carrying no
      recognised role.
    * **Tenant isolation** — applied inside ``TenantScopedRepository``, so this
      endpoint cannot return another tenant's users even if edited carelessly.
    """
    users, total = await repository.list(params)
    return Page[UserRead].build(
        items=[UserRead.model_validate(user) for user in users],
        page=params.page,
        size=params.size,
        total_items=total,
    )


# Declared before "/{user_id}": that route would otherwise claim "invitations"
# as an id and answer 422.
@router.get("/invitations", response_model=list[InvitationRead], summary="Open invitations")
async def list_invitations(session: DbSession, _authorized: RequireAdmin) -> list[InvitationRead]:
    invitations = await TeamInvitationService(session).list_open()
    return [InvitationRead.model_validate(i) for i in invitations]


@router.post(
    "/invitations",
    response_model=InvitationRead,
    status_code=status.HTTP_201_CREATED,
    summary="Invite someone to the workspace",
    dependencies=[Depends(_invite_limit)],
)
async def create_invitation(
    payload: InvitationCreate, session: DbSession, _authorized: RequireAdmin, user: CurrentUser
) -> InvitationRead:
    """Emails a one-time link. Inviting an address that already has an open
    invitation re-sends it with a fresh link and the given role."""
    invitation = await TeamInvitationService(session).invite(
        email=payload.email, role=RoleName(payload.role), inviter=user
    )
    return InvitationRead.model_validate(invitation)


@router.delete(
    "/invitations/{invitation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke an open invitation",
)
async def revoke_invitation(
    invitation_id: uuid.UUID, session: DbSession, _authorized: RequireAdmin
) -> None:
    await TeamInvitationService(session).revoke(invitation_id)


@router.get(
    "/{user_id}",
    response_model=UserRead,
    summary="Fetch a single user",
)
async def get_user(
    repository: UserRepo,
    user_id: Annotated[uuid.UUID, Path(description="Identifier of the user to fetch.")],
    _authorized: RequireViewer,
) -> UserRead:
    """Return one user by id.

    A user belonging to a different tenant is reported as 404 rather than 403,
    so that identifiers cannot be probed for existence across tenants.

    Note the ordering of the two failure modes: authorization is decided first,
    so an unauthorized caller receives 403 without the endpoint ever revealing
    whether the requested id exists.
    """
    user = await repository.get_by_id_or_raise(user_id)
    return UserRead.model_validate(user)
