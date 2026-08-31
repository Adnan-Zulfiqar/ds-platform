"""LEGAL-T1: the publication gate and the server's authority over the version.

The Terms are a draft. Two things follow, and neither can be left to the
frontend: a deployed environment must not form a contract on unapproved text at
all, and no client may choose which version it claims to have accepted.

Nothing here contacts a provider or sends mail.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import AsyncClient

from app.core.config import Environment, settings
from app.core.legal import PRIVACY_NOTICE_VERSION, TERMS_PUBLISHED, TERMS_VERSION
from app.services.auth import LegalAcceptance
from tests.integration.conftest import STRONG_PASSWORD, registration_payload

pytestmark = pytest.mark.integration


@pytest.fixture
def deployed_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make `settings.environment` report a deployed environment.

    Patched on the settings object rather than rebuilt from the environment,
    because `Settings` refuses to construct a production configuration without a
    full set of production secrets — and the point here is the legal gate, not
    the secret guards, which have their own tests.
    """
    monkeypatch.setattr(settings, "environment", Environment.PRODUCTION)


class TestTheDraftIsMarkedAsOne:
    """A version identifier is looked up later to answer "agreed to what?".

    If the answer is a draft, the identifier has to say so on its face.
    """

    def test_the_version_is_a_draft_identifier(self) -> None:
        assert TERMS_VERSION.startswith("draft-"), TERMS_VERSION

    def test_the_terms_are_not_marked_published(self) -> None:
        """This milestone deliberately does not flip the flag."""
        assert TERMS_PUBLISHED is False

    def test_the_placeholder_sentinel_is_gone(self) -> None:
        """`"unpublished"` was a stand-in for a document that did not exist."""
        assert TERMS_VERSION != "unpublished"


class TestDeployedSignupFailsClosedWhileUnpublished:
    """A stored row saying somebody agreed to a draft is worse than no row.

    It looks like evidence of a contract, and it is evidence of agreement to
    text nobody approved. So a deployed environment refuses the registration
    rather than recording it.
    """

    def test_a_deployed_environment_refuses_registration(self, deployed_environment: None) -> None:
        from app.core.legal import TermsNotPublishedError

        acceptance = LegalAcceptance(
            terms_accepted=True,
            privacy_accepted=True,
            terms_version=TERMS_VERSION,
            privacy_version=PRIVACY_NOTICE_VERSION,
        )

        with pytest.raises(TermsNotPublishedError):
            acceptance.require_valid()

    def test_the_refusal_comes_before_the_acceptance_checks(
        self, deployed_environment: None
    ) -> None:
        """Otherwise the caller is told to tick a box that would not help.

        A perfectly-formed acceptance and a malformed one must both be refused
        for the same reason: the service is not open for new contracts.
        """
        from app.core.legal import TermsNotPublishedError

        malformed = LegalAcceptance(
            terms_accepted=False,
            privacy_accepted=False,
            terms_version="whatever",
            privacy_version="whatever",
        )

        with pytest.raises(TermsNotPublishedError):
            malformed.require_valid()

    def test_the_refusal_carries_its_own_error_code(self, deployed_environment: None) -> None:
        """`legal_acceptance_required` would tell the caller to try again."""
        from app.core.legal import TermsNotPublishedError

        assert TermsNotPublishedError.code == "terms_not_published"
        assert TermsNotPublishedError().status_code == 422

    def test_the_message_does_not_blame_the_caller(self, deployed_environment: None) -> None:
        from app.core.legal import TermsNotPublishedError

        acceptance = LegalAcceptance(
            terms_accepted=True,
            privacy_accepted=True,
            terms_version=TERMS_VERSION,
            privacy_version=PRIVACY_NOTICE_VERSION,
        )
        with pytest.raises(TermsNotPublishedError) as caught:
            acceptance.require_valid()

        message = str(caught.value).lower()
        # It must not instruct the caller to do something that would not help.
        assert "you must" not in message
        assert "tick" not in message
        assert "reload" not in message

    @pytest.mark.parametrize("environment", [Environment.LOCAL, Environment.TEST])
    def test_local_and_test_environments_are_exempt(
        self, monkeypatch: pytest.MonkeyPatch, environment: Environment
    ) -> None:
        """The flow has to be buildable and testable before it is publishable."""
        monkeypatch.setattr(settings, "environment", environment)

        LegalAcceptance(
            terms_accepted=True,
            privacy_accepted=True,
            terms_version=TERMS_VERSION,
            privacy_version=PRIVACY_NOTICE_VERSION,
        ).require_valid()

    async def test_the_endpoint_refuses_a_deployed_signup(
        self, client: AsyncClient, deployed_environment: None
    ) -> None:
        """Through HTTP, not only through the value object."""
        response = await client.post(
            "/api/v1/auth/register",
            json=registration_payload(email=f"t1-{uuid.uuid4().hex}@example.com"),
        )

        assert response.status_code == 422, response.text
        assert response.json()["code"] == "terms_not_published"


class TestTheServerOwnsTheVersion:
    """A client that can name the version it accepted can name any version."""

    async def test_an_invented_terms_version_is_refused(self, client: AsyncClient) -> None:
        response = await client.post(
            "/api/v1/auth/register",
            json=registration_payload(
                email=f"t1-{uuid.uuid4().hex}@example.com", termsVersion="v1.0-final"
            ),
        )

        assert response.status_code == 422, response.text
        assert response.json()["code"] == "legal_acceptance_required"

    async def test_the_previous_sentinel_is_no_longer_accepted(self, client: AsyncClient) -> None:
        """A client built before this milestone must not be able to register.

        It would be quoting a version that no longer describes anything.
        """
        response = await client.post(
            "/api/v1/auth/register",
            json=registration_payload(
                email=f"t1-{uuid.uuid4().hex}@example.com", termsVersion="unpublished"
            ),
        )

        assert response.status_code == 422, response.text

    async def test_an_invented_privacy_version_is_refused(self, client: AsyncClient) -> None:
        response = await client.post(
            "/api/v1/auth/register",
            json=registration_payload(
                email=f"t1-{uuid.uuid4().hex}@example.com", privacyVersion="2020-01-01"
            ),
        )

        assert response.status_code == 422, response.text

    @pytest.mark.parametrize("field", ["termsAccepted", "privacyAccepted"])
    async def test_a_false_acceptance_flag_is_refused(
        self, client: AsyncClient, field: str
    ) -> None:
        payload: dict[str, Any] = registration_payload(email=f"t1-{uuid.uuid4().hex}@example.com")
        payload[field] = False

        response = await client.post("/api/v1/auth/register", json=payload)

        assert response.status_code == 422, response.text
        assert response.json()["code"] == "legal_acceptance_required"

    @pytest.mark.parametrize("field", ["termsAccepted", "privacyAccepted", "termsVersion"])
    async def test_an_omitted_acceptance_field_is_refused(
        self, client: AsyncClient, field: str
    ) -> None:
        payload: dict[str, Any] = registration_payload(email=f"t1-{uuid.uuid4().hex}@example.com")
        payload.pop(field)

        response = await client.post("/api/v1/auth/register", json=payload)

        assert response.status_code == 422, response.text

    async def test_the_recorded_version_is_the_servers_own(
        self, client: AsyncClient, db_session: Any
    ) -> None:
        """What is stored is the server's constant, not the request's string."""
        from sqlalchemy import select

        from app.models.user import User

        email = f"t1-{uuid.uuid4().hex}@example.com"
        response = await client.post(
            "/api/v1/auth/register", json=registration_payload(email=email)
        )
        assert response.status_code == 201, response.text

        user = (await db_session.execute(select(User).where(User.email == email))).scalar_one()

        assert user.terms_version == TERMS_VERSION
        assert user.privacy_version == PRIVACY_NOTICE_VERSION
        assert user.terms_accepted_at is not None
        assert user.privacy_accepted_at is not None


class TestExistingUsersAreUnaffected:
    """The gate is on account creation, not on the people who already have one.

    Locking existing customers out of a product they are using, because a
    document they already accepted is being redrafted, would be an outage
    dressed as diligence.
    """

    async def test_an_existing_user_can_still_sign_in(self, client: AsyncClient) -> None:
        email = f"t1-{uuid.uuid4().hex}@example.com"
        created = await client.post("/api/v1/auth/register", json=registration_payload(email=email))
        assert created.status_code == 201, created.text

        response = await client.post(
            "/api/v1/auth/login", json={"email": email, "password": STRONG_PASSWORD}
        )

        assert response.status_code == 200, response.text

    async def test_sign_in_still_works_in_a_deployed_environment(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Registration is closed; the door for existing customers is not."""
        email = f"t1-{uuid.uuid4().hex}@example.com"
        created = await client.post("/api/v1/auth/register", json=registration_payload(email=email))
        assert created.status_code == 201, created.text

        monkeypatch.setattr(settings, "environment", Environment.PRODUCTION)
        response = await client.post(
            "/api/v1/auth/login", json={"email": email, "password": STRONG_PASSWORD}
        )

        assert response.status_code == 200, response.text

    async def test_no_stored_row_carries_the_old_sentinel(
        self, client: AsyncClient, db_session: Any
    ) -> None:
        """No backfill was performed, and none was needed.

        Nothing in this milestone rewrites what an existing user accepted, which
        is the point: a stored acceptance is a record of what happened, not a
        field to be tidied.
        """
        from sqlalchemy import func, select

        from app.models.user import User

        email = f"t1-{uuid.uuid4().hex}@example.com"
        assert (
            await client.post("/api/v1/auth/register", json=registration_payload(email=email))
        ).status_code == 201

        sentinel_rows = (
            await db_session.execute(
                select(func.count()).select_from(User).where(User.terms_version == "unpublished")
            )
        ).scalar_one()

        # In a fresh test database this is zero because nothing writes it any
        # more. On a real deployment the historical rows stay exactly as they
        # were, which is why no migration is part of this change.
        assert sentinel_rows == 0


class TestNoMigrationWasNeeded:
    """The existing columns already hold an arbitrary version string."""

    def test_the_acceptance_columns_take_the_draft_identifier(self) -> None:
        from app.models.user import User

        column = User.__table__.c.terms_version
        assert column.type.length is None or column.type.length >= len(TERMS_VERSION)
        assert column.nullable is True


class TestTheRecordedDecisionsSurvive:
    """The blockers and the approvals are documentation, and documentation rots.

    Each item below was a deliberate decision with a reason behind it, and each
    is one tidy-up away from disappearing. A publication checklist that has
    quietly lost its backup gate is worse than no checklist, because somebody
    will read it and believe they are done. These are cheap assertions against
    an expensive mistake.
    """

    @staticmethod
    def _doc(name: str) -> str:
        import pathlib

        root = pathlib.Path(__file__).resolve().parents[3]
        return (root / "docs" / "legal" / name).read_text(encoding="utf-8")

    def test_the_hundred_pound_floor_is_recorded_as_operator_approved(self) -> None:
        """It was a drafting proposal in LEGAL-T1. It is a decision now."""
        review = self._doc("TERMS_LEGAL_REVIEW.md")

        assert "£100" in review
        assert "Operator-approved" in review
        assert "operator-approved" in review.lower()

    def test_the_floor_is_not_recorded_as_solicitor_approved(self) -> None:
        """Operator approval of a figure is not legal approval of a mechanism.

        Asserted on the explicit disclaimer rather than the absence of a phrase:
        the document *does* contain "solicitor-approved", inside the sentence
        saying these decisions are not. A substring check cannot tell a denial
        from a claim, so it checks the denial is there.
        """
        review = self._doc("TERMS_LEGAL_REVIEW.md")

        assert "None of them is legal approval" in review
        assert "**solicitor**-approved" in review
        assert "the mechanism is not legally reviewed" in self._doc(
            "TERMS_PUBLICATION_CHECKLIST.md"
        )

    def test_the_backup_blocker_is_intact(self) -> None:
        """The data-loss exclusion was approved *on condition* of this."""
        checklist = self._doc("TERMS_PUBLICATION_CHECKLIST.md")
        review = self._doc("TERMS_LEGAL_REVIEW.md")

        assert "restoration has been tested" in checklist
        assert "Backups — there are none" in checklist
        assert "Backup restoration has been tested" in review
        assert "does not weaken the backup blocker" in review

    def test_the_solicitor_review_blocker_is_intact(self) -> None:
        checklist = self._doc("TERMS_PUBLICATION_CHECKLIST.md")

        assert "solicitor has reviewed the loss-of-data exclusion" in checklist
        assert "Solicitor review and approval of the Terms" in checklist

    def test_the_checklist_requires_support_mailbox_verification(self) -> None:
        """`/terms` sends contractual notices there. Nobody has tested it."""
        checklist = self._doc("TERMS_PUBLICATION_CHECKLIST.md")

        assert "support@whiteto.com` exists and inbound delivery has been verified" in checklist
        assert "no mail was sent" in checklist.lower()

    def test_no_document_claims_a_mailbox_was_tested(self) -> None:
        for name in (
            "TERMS_LEGAL_REVIEW.md",
            "TERMS_PUBLICATION_CHECKLIST.md",
            "TERMS_PRODUCT_AUDIT.md",
        ):
            text = self._doc(name).lower()
            assert "mailbox has been tested" not in text.replace(
                "neither mailbox has been tested", ""
            )
            assert "delivery verified on the same basis" in text or "verified" in text

    def test_the_contact_split_is_recorded(self) -> None:
        review = self._doc("TERMS_LEGAL_REVIEW.md")

        assert "support@whiteto.com" in review
        assert "privacy@whiteto.com" in review
        assert "data-subject requests" in review
