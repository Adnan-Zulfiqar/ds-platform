"""Erasure must remove the right person and nobody else.

The dangerous failure here is not "it missed a row" — it is "it erased the
neighbouring tenant", which is the same class of defect the repository layer
exists to prevent. These tests therefore spend most of their effort on the
second tenant that must survive untouched.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.services.data_subject_erasure import DataSubjectErasureService
from tests.integration.conftest import registration_payload

pytestmark = pytest.mark.integration


async def register(client: AsyncClient, email: str) -> uuid.UUID:
    response = await client.post("/api/v1/auth/register", json=registration_payload(email=email))
    assert response.status_code == 201, response.text
    return uuid.UUID(response.json()["identity"]["user"]["id"])


class TestResolution:
    async def test_an_unknown_address_resolves_to_nothing(self, db_session: AsyncSession) -> None:
        service = DataSubjectErasureService(db_session)
        assert await service.resolve("nobody-at-all@example.com") is None

    async def test_an_address_resolves_regardless_of_case_and_padding(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        email = f"case-{uuid.uuid4().hex}@example.com"
        user_id = await register(client, email)

        service = DataSubjectErasureService(db_session)
        subject = await service.resolve(f"  {email.upper()}  ")

        assert subject is not None
        assert subject.user_id == user_id


class TestDryRun:
    async def test_planning_reports_counts_and_writes_nothing(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        email = f"plan-{uuid.uuid4().hex}@example.com"
        await register(client, email)

        service = DataSubjectErasureService(db_session)
        subject = await service.resolve(email)
        assert subject is not None

        plan = await service.plan(subject)

        assert plan.executed is False
        assert plan.counts["user"] == 1
        # Registration mints a refresh token, so there is something real to find.
        assert plan.counts["refresh_tokens"] >= 1

        # Nothing changed.
        still_there = (
            await db_session.execute(select(User).where(User.id == subject.user_id))
        ).scalar_one()
        assert still_there.email == email
        assert still_there.is_active is True


class TestErasure:
    async def test_it_anonymises_the_user_and_destroys_credentials(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        email = f"erase-{uuid.uuid4().hex}@example.com"
        await register(client, email)

        service = DataSubjectErasureService(db_session)
        subject = await service.resolve(email)
        assert subject is not None

        outcome = await service.erase(subject)
        await db_session.flush()

        assert outcome.executed is True
        assert outcome.counts["user"] == 1

        user = (
            await db_session.execute(select(User).where(User.id == subject.user_id))
        ).scalar_one()
        assert user.email != email
        assert user.email.endswith("@erased.invalid")
        assert user.first_name is None
        assert user.last_name is None
        assert user.password_hash is None
        assert user.is_active is False

        remaining = (
            (
                await db_session.execute(
                    select(RefreshToken).where(RefreshToken.user_id == subject.user_id)
                )
            )
            .scalars()
            .all()
        )
        assert remaining == []

    async def test_the_original_address_no_longer_resolves(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        email = f"gone-{uuid.uuid4().hex}@example.com"
        await register(client, email)

        service = DataSubjectErasureService(db_session)
        subject = await service.resolve(email)
        assert subject is not None
        await service.erase(subject)
        await db_session.flush()

        assert await service.resolve(email) is None

    async def test_a_second_run_reports_zeroes_rather_than_failing(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        email = f"twice-{uuid.uuid4().hex}@example.com"
        await register(client, email)

        service = DataSubjectErasureService(db_session)
        subject = await service.resolve(email)
        assert subject is not None

        await service.erase(subject)
        await db_session.flush()
        second = await service.erase(subject)
        await db_session.flush()

        assert second.counts["refresh_tokens"] == 0
        assert second.counts["user"] == 1  # the anonymising update is a no-op rewrite


class TestTenantIsolation:
    async def test_erasing_one_tenant_leaves_another_untouched(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """The failure this whole module is shaped to prevent."""
        victim_email = f"victim-{uuid.uuid4().hex}@example.com"
        bystander_email = f"bystander-{uuid.uuid4().hex}@example.com"
        await register(client, victim_email)
        bystander_id = await register(client, bystander_email)

        service = DataSubjectErasureService(db_session)
        subject = await service.resolve(victim_email)
        assert subject is not None
        await service.erase(subject)
        await db_session.flush()

        bystander = (
            await db_session.execute(select(User).where(User.id == bystander_id))
        ).scalar_one()
        assert bystander.email == bystander_email
        assert bystander.is_active is True
        assert bystander.password_hash is not None

        bystander_tokens = (
            (
                await db_session.execute(
                    select(RefreshToken).where(RefreshToken.user_id == bystander_id)
                )
            )
            .scalars()
            .all()
        )
        assert bystander_tokens != []


class TestDeclaration:
    def test_every_declared_category_names_an_action(self) -> None:
        # The declaration is the audit artefact the runbook is written from; an
        # entry with no action is a category nobody decided about.
        for category in DataSubjectErasureService.categories():
            assert category.name
            assert category.action in {"delete", "anonymise"}
            assert category.note

    async def test_the_subject_repr_carries_no_personal_data(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        # This string reaches log lines. An email address in it would mean the
        # erasure leaves the address behind in the logs it was meant to remove.
        email = f"norepr-{uuid.uuid4().hex}@example.com"
        await register(client, email)

        service = DataSubjectErasureService(db_session)
        subject = await service.resolve(email)
        assert subject is not None

        rendered = str(subject)
        assert email not in rendered
        assert "@" not in rendered
