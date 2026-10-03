"""Team invitation schemas (Track E4). The link secret never appears in a
response: it exists only in the email."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import EmailStr, Field, SecretStr

from app.schemas.auth import LegalAcceptanceFields
from app.schemas.base import CamelCaseModel, IdentifiedSchema


class InvitationCreate(CamelCaseModel):
    email: EmailStr
    role: Literal["admin", "member", "viewer"] = "member"


class InvitationRead(IdentifiedSchema):
    email: str
    role: str
    expires_at: datetime


class InvitationTokenRequest(CamelCaseModel):
    """The link's token, sent in a body rather than a path or query string so
    it stays out of access logs."""

    token: str = Field(min_length=36, max_length=200)


class InvitationPreviewRead(CamelCaseModel):
    email: str
    role: str
    workspace_name: str
    expires_at: datetime


class InvitationAcceptRequest(LegalAcceptanceFields):
    token: str = Field(min_length=36, max_length=200)
    password: SecretStr
    first_name: str | None = Field(default=None, max_length=128)
    last_name: str | None = Field(default=None, max_length=128)


__all__ = [
    "InvitationAcceptRequest",
    "InvitationCreate",
    "InvitationPreviewRead",
    "InvitationRead",
    "InvitationTokenRequest",
]
