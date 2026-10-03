"""Track E4 — team invitations, end to end through the API.

Real Postgres (migrations), the recording email provider. The link secret is
read from the recorded email, as a recipient would.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.legal import PRIVACY_NOTICE_VERSION, TERMS_VERSION
from app.integrations.email import provider as email_provider
from app.integrations.email.provider import EmailDeliveryError, StubEmailProvider
from app.models.invitation import UserInvitation
from tests.integration.conftest import STRONG_PASSWORD
from tests.integration.test_ebay_c1_api import auth_header, register, token_with_roles

pytestmark = pytest.mark.integration

INVITES = "/api/v1/users/invitations"
PREVIEW = "/api/v1/auth/invitations/preview"
ACCEPT = "/api/v1/auth/invitations/accept"


@pytest.fixture
def mailbox() -> Iterator[StubEmailProvider]:
    email_provider.reset_email_provider()
    stub = email_provider.get_email_provider()
    assert isinstance(stub, StubEmailProvider)
    yield stub
    email_provider.reset_email_provider()


def link_token(mailbox: StubEmailProvider) -> str:
    match = re.search(r"/invite#token=([\w.-]+)", mailbox.sent[-1].text)
    assert match, mailbox.sent[-1].text
    return match.group(1)


def accept_payload(token: str, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "token": token,
        "password": STRONG_PASSWORD,
        "firstName": "Grace",
        "lastName": "Hopper",
        "termsAccepted": True,
        "privacyAccepted": True,
        "termsVersion": TERMS_VERSION,
        "privacyVersion": PRIVACY_NOTICE_VERSION,
    }
    payload.update(overrides)
    return payload


def new_address() -> str:
    return f"invitee+{uuid.uuid4().hex[:8]}@example.com"


async def invite(
    client: AsyncClient, owner: dict[str, Any], email: str, role: str = "member"
) -> dict[str, Any]:
    response = await client.post(
        INVITES, json={"email": email, "role": role}, headers=auth_header(owner)
    )
    assert response.status_code == 201, response.text
    result: dict[str, Any] = response.json()
    return result


async def test_an_invited_person_joins_with_the_given_role_and_is_signed_in(
    client: AsyncClient, mailbox: StubEmailProvider
) -> None:
    owner = await register(client, companyName="Invite Co")
    address = new_address()
    created = await invite(client, owner, address, "viewer")
    assert "token" not in created and "tokenHash" not in created
    assert [m.to for m in mailbox.sent] == [address]
    assert "Invite Co" in mailbox.sent[0].subject

    listed = await client.get(INVITES, headers=auth_header(owner))
    assert [i["email"] for i in listed.json()] == [address]

    token = link_token(mailbox)
    preview = await client.post(PREVIEW, json={"token": token})
    assert preview.status_code == 200
    assert preview.json()["workspaceName"] == "Invite Co"
    assert preview.json()["role"] == "viewer"

    accepted = await client.post(ACCEPT, json=accept_payload(token))
    assert accepted.status_code == 201, accepted.text
    body = accepted.json()
    assert body["identity"]["user"]["email"] == address
    assert body["identity"]["tenant"]["id"] == owner["identity"]["tenant"]["id"]
    assert body["identity"]["roles"] == ["viewer"]

    login = await client.post(
        "/api/v1/auth/login", json={"email": address, "password": STRONG_PASSWORD}
    )
    assert login.status_code == 200
    assert (await client.get(INVITES, headers=auth_header(owner))).json() == []
    # A link works once.
    assert (await client.post(ACCEPT, json=accept_payload(token))).status_code == 404


async def test_reinviting_rotates_the_link_and_revoking_kills_it(
    client: AsyncClient, mailbox: StubEmailProvider
) -> None:
    owner = await register(client)
    address = new_address()
    await invite(client, owner, address)
    first = link_token(mailbox)
    again = await invite(client, owner, address, "admin")
    second = link_token(mailbox)
    assert first != second
    assert (await client.post(PREVIEW, json={"token": first})).status_code == 404
    assert (await client.post(PREVIEW, json={"token": second})).json()["role"] == "admin"

    revoked = await client.delete(f"{INVITES}/{again['id']}", headers=auth_header(owner))
    assert revoked.status_code == 204
    assert (await client.post(PREVIEW, json={"token": second})).status_code == 404


async def test_a_link_cannot_be_redirected_to_another_workspace(
    client: AsyncClient, mailbox: StubEmailProvider
) -> None:
    first = await register(client)
    second = await register(client)
    await invite(client, first, new_address())
    _, secret = link_token(mailbox).split(".", 1)
    forged = f"{second['identity']['tenant']['id']}.{secret}"
    assert (await client.post(PREVIEW, json={"token": forged})).status_code == 404
    assert (await client.post(ACCEPT, json=accept_payload(forged))).status_code == 404
    # And the other workspace's admin cannot see it.
    assert (await client.get(INVITES, headers=auth_header(second))).json() == []


async def test_an_expired_link_is_refused(
    client: AsyncClient, db_session: AsyncSession, mailbox: StubEmailProvider
) -> None:
    owner = await register(client)
    created = await invite(client, owner, new_address())
    await db_session.execute(
        sa.update(UserInvitation)
        .where(UserInvitation.id == uuid.UUID(created["id"]))
        .values(expires_at=datetime.now(UTC) - timedelta(minutes=1))
    )
    await db_session.commit()
    response = await client.post(ACCEPT, json=accept_payload(link_token(mailbox)))
    assert response.status_code == 404


async def test_who_may_invite_and_whom(client: AsyncClient, mailbox: StubEmailProvider) -> None:
    owner = await register(client)
    member = token_with_roles(owner, "member")
    response = await client.post(INVITES, json={"email": new_address()}, headers=member)
    assert response.status_code == 403
    response = await client.post(
        INVITES, json={"email": new_address(), "role": "owner"}, headers=auth_header(owner)
    )
    assert response.status_code == 422
    response = await client.post(
        INVITES,
        json={"email": owner["identity"]["user"]["email"]},
        headers=auth_header(owner),
    )
    assert response.status_code == 409
    assert mailbox.sent == []


async def test_an_address_with_an_account_elsewhere_cannot_accept(
    client: AsyncClient, mailbox: StubEmailProvider
) -> None:
    elsewhere = await register(client)
    owner = await register(client)
    await invite(client, owner, elsewhere["identity"]["user"]["email"])
    response = await client.post(ACCEPT, json=accept_payload(link_token(mailbox)))
    assert response.status_code == 409


async def test_acceptance_requires_the_terms_and_a_strong_password(
    client: AsyncClient, mailbox: StubEmailProvider
) -> None:
    owner = await register(client)
    await invite(client, owner, new_address())
    token = link_token(mailbox)
    refused = await client.post(ACCEPT, json=accept_payload(token, termsAccepted=False))
    assert refused.status_code == 422
    weak = await client.post(ACCEPT, json=accept_payload(token, password="short"))
    assert weak.status_code == 422
    # Neither attempt used up the link.
    assert (await client.post(ACCEPT, json=accept_payload(token))).status_code == 201


async def test_an_email_failure_leaves_no_invitation_behind(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, mailbox: StubEmailProvider
) -> None:
    owner = await register(client)

    async def down(_message: Any) -> str:
        raise EmailDeliveryError()

    monkeypatch.setattr(mailbox, "send", down)
    response = await client.post(INVITES, json={"email": new_address()}, headers=auth_header(owner))
    assert response.status_code == 503
    assert response.json()["code"] == "invitation_email_failed"
    assert (await client.get(INVITES, headers=auth_header(owner))).json() == []
