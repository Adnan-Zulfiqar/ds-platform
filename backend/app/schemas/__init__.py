"""Pydantic schemas — the API contract.

Request and response shapes live here. ORM models never cross the API boundary
directly; see ``app.schemas.base`` for why.
"""

from app.schemas.auth import (
    AuthenticatedIdentity,
    AuthResponse,
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    RegisterRequest,
    TenantRead,
    TokenResponse,
)
from app.schemas.base import AppBaseModel, CamelCaseModel, IdentifiedSchema, ORMBaseModel
from app.schemas.common import (
    ComponentHealth,
    ErrorDetail,
    ErrorResponse,
    HealthResponse,
    HealthStatus,
    ListQueryParams,
    MessageResponse,
    Page,
    PageMeta,
    PaginationParams,
    SearchParams,
    SortDirection,
    SortParams,
    list_query_params,
)

__all__ = [
    "AppBaseModel",
    "AuthResponse",
    "AuthenticatedIdentity",
    "CamelCaseModel",
    "ComponentHealth",
    "ErrorDetail",
    "ErrorResponse",
    "HealthResponse",
    "HealthStatus",
    "IdentifiedSchema",
    "ListQueryParams",
    "LoginRequest",
    "LogoutRequest",
    "MessageResponse",
    "ORMBaseModel",
    "Page",
    "PageMeta",
    "PaginationParams",
    "RefreshRequest",
    "RegisterRequest",
    "SearchParams",
    "SortDirection",
    "SortParams",
    "TenantRead",
    "TokenResponse",
    "list_query_params",
]
