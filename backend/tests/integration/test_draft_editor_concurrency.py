"""M2A — optimistic concurrency and edit-gating for the draft editor.

Covers the two write-path guarantees ``ProductService.update_draft`` adds on
top of the pre-existing ``PATCH /drafts/{id}`` (`test_draft_editor.py`):

1. A stale save (the row changed since the caller loaded it) is rejected
   with a 409, never silently applied — ``ProductRepository.update_if_unmodified_since``.
2. A product that has already been published to a channel cannot be edited
   back through this drafts-only endpoint.

Also proves the M1-era supplier-snapshot separation still holds through the
new write path, and that a no-op save neither writes nor moves ``updatedAt``.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import set_tenant_id
from app.core.tokens import create_access_token
from app.models.product import Product, ProductSource, ProductStatus
from app.models.shopify import ListingSyncStatus, StoreListing
from app.models.store import Store, StorePlatform
from tests.integration.test_products import auth_header, register

pytestmark = pytest.mark.integration

DRAFTS_URL = "/api/v1/drafts"


async def _seed(client: AsyncClient, db_session: AsyncSession) -> tuple[dict[str, str], Product]:
    body = await register(client)
    headers = auth_header(body)
    tenant_id = uuid.UUID(str(body["identity"]["tenant"]["id"]))
    set_tenant_id(tenant_id)
    product = Product(
        tenant_id=tenant_id,
        source=ProductSource.MANUAL,
        external_id=f"manual-{uuid.uuid4().hex[:12]}",
        title="Editable draft title",
        status=ProductStatus.DRAFT,
        description="<p>Original</p>",
        supplier_description="<p>Original</p>",
        supplier_title="Editable draft title",
        supplier_brand="Original Brand",
        brand="Original Brand",
    )
    db_session.add(product)
    await db_session.flush()
    return headers, product


async def _publish(db_session: AsyncSession, product: Product) -> None:
    """Give `product` a synced StoreListing -- the same condition that moves
    a row from Drafts to Products (`ProductRepository._synced_listing_exists`)."""
    store = Store(
        tenant_id=product.tenant_id,
        name="Test Shopify Store",
        slug=f"store-{uuid.uuid4().hex[:8]}",
        platform=StorePlatform.SHOPIFY,
        currency="USD",
    )
    db_session.add(store)
    await db_session.flush()
    listing = StoreListing(
        tenant_id=product.tenant_id,
        store_id=store.id,
        product_id=product.id,
        external_product_id="gid://shopify/Product/123",
        status=ListingSyncStatus.SYNCED,
    )
    db_session.add(listing)
    await db_session.flush()


class TestOptimisticConcurrency:
    async def test_get_draft_exposes_updated_at(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers, product = await _seed(client, db_session)
        response = await client.get(f"{DRAFTS_URL}/{product.id}", headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()["updatedAt"], (
            "updatedAt must be present to round-trip as a version token"
        )

    async def test_save_with_the_current_version_succeeds(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers, product = await _seed(client, db_session)
        loaded = (await client.get(f"{DRAFTS_URL}/{product.id}", headers=headers)).json()

        response = await client.patch(
            f"{DRAFTS_URL}/{product.id}",
            headers=headers,
            json={"title": "Updated Title", "expectedUpdatedAt": loaded["updatedAt"]},
        )
        assert response.status_code == 200, response.text
        assert response.json()["title"] == "Updated Title"

    async def test_a_stale_save_is_rejected_with_409_and_does_not_overwrite(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """The exact scenario the M2A brief requires: Editor A loads, Editor
        B loads the same draft, A saves, B's (now-stale) save must be
        rejected -- not silently applied over A's change.

        "A saves" is simulated as a direct row write rather than a second
        `client.patch()` call: this test's `client`/`db_session` fixtures
        share one transaction for the whole test, and Postgres's `now()` is
        frozen for a transaction's duration -- a real second HTTP round-trip
        here would not actually advance `updated_at`, which would make this
        test pass or fail for the wrong reason. Moving the row's
        `updated_at` directly is the same fact ("this row was written by
        someone else") from the database's point of view, without depending
        on wall-clock time the test harness cannot provide.
        """
        headers, product = await _seed(client, db_session)

        editor_b = (await client.get(f"{DRAFTS_URL}/{product.id}", headers=headers)).json()

        concurrent_write_at = product.updated_at + timedelta(seconds=5)
        await db_session.execute(
            update(Product)
            .where(Product.id == product.id)
            .values(title="Editor A's Title", updated_at=concurrent_write_at)
        )
        await db_session.flush()

        b_save = await client.patch(
            f"{DRAFTS_URL}/{product.id}",
            headers=headers,
            json={"title": "Editor B's Title", "expectedUpdatedAt": editor_b["updatedAt"]},
        )
        assert b_save.status_code == 409, b_save.text
        assert b_save.json()["code"]

        current = await client.get(f"{DRAFTS_URL}/{product.id}", headers=headers)
        assert current.json()["title"] == "Editor A's Title", (
            "Editor B's stale save must never have been applied"
        )

    async def test_omitting_expected_updated_at_keeps_last_write_wins(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Backward compatibility: a caller that does not yet send a version
        token gets the exact pre-M2A behaviour."""
        headers, product = await _seed(client, db_session)

        await client.get(f"{DRAFTS_URL}/{product.id}", headers=headers)  # editor loads, ignored

        response = await client.patch(
            f"{DRAFTS_URL}/{product.id}", headers=headers, json={"title": "No Token Sent"}
        )
        assert response.status_code == 200, response.text
        assert response.json()["title"] == "No Token Sent"

    async def test_repeated_saves_with_fresh_versions_do_not_corrupt_state(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers, product = await _seed(client, db_session)

        current = (await client.get(f"{DRAFTS_URL}/{product.id}", headers=headers)).json()
        for index in range(3):
            saved = await client.patch(
                f"{DRAFTS_URL}/{product.id}",
                headers=headers,
                json={"title": f"Title {index}", "expectedUpdatedAt": current["updatedAt"]},
            )
            assert saved.status_code == 200, saved.text
            current = saved.json()

        assert current["title"] == "Title 2"

    async def test_a_true_no_op_save_does_not_move_updated_at(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Resaving identical values must not bump `updatedAt` -- doing so
        would misrepresent "last edited" and could spuriously conflict a
        concurrent editor who made a real change in between."""
        headers, product = await _seed(client, db_session)
        loaded = (await client.get(f"{DRAFTS_URL}/{product.id}", headers=headers)).json()

        response = await client.patch(
            f"{DRAFTS_URL}/{product.id}",
            headers=headers,
            json={"title": loaded["title"], "expectedUpdatedAt": loaded["updatedAt"]},
        )
        assert response.status_code == 200, response.text
        assert response.json()["updatedAt"] == loaded["updatedAt"]

    async def test_a_true_no_op_save_succeeds_even_with_a_stale_version_token(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """A resave of unchanged values must not be treated as a conflict
        just because something else touched the row since -- there is
        nothing to overwrite, so the no-op short-circuit (which runs before
        any version check) must let it through even against a now-stale
        `expectedUpdatedAt`. This is what actually proves the no-op filter
        runs first; the sibling test above cannot distinguish "no write
        happened" from "a write happened but this test's frozen transaction
        timestamp didn't move" (see the stale-save test's docstring)."""
        headers, product = await _seed(client, db_session)
        loaded = (await client.get(f"{DRAFTS_URL}/{product.id}", headers=headers)).json()

        concurrent_write_at = product.updated_at + timedelta(seconds=5)
        await db_session.execute(
            update(Product).where(Product.id == product.id).values(updated_at=concurrent_write_at)
        )
        await db_session.flush()

        response = await client.patch(
            f"{DRAFTS_URL}/{product.id}",
            headers=headers,
            json={"title": loaded["title"], "expectedUpdatedAt": loaded["updatedAt"]},
        )
        assert response.status_code == 200, response.text


class TestPublishedDraftIsNotEditableHere:
    async def test_a_published_product_cannot_be_saved_through_the_drafts_endpoint(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers, product = await _seed(client, db_session)
        await _publish(db_session, product)

        response = await client.patch(
            f"{DRAFTS_URL}/{product.id}",
            headers=headers,
            json={"title": "Should Not Land"},
        )
        assert response.status_code == 409, response.text

        row = (
            await db_session.execute(select(Product).where(Product.id == product.id))
        ).scalar_one()
        assert row.title != "Should Not Land"

    async def test_an_unpublished_draft_is_unaffected_by_the_publish_gate(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers, product = await _seed(client, db_session)

        response = await client.patch(
            f"{DRAFTS_URL}/{product.id}", headers=headers, json={"title": "Still A Draft"}
        )
        assert response.status_code == 200, response.text


class TestSupplierSnapshotImmutability:
    async def test_editing_title_and_brand_never_changes_the_supplier_twins(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers, product = await _seed(client, db_session)

        response = await client.patch(
            f"{DRAFTS_URL}/{product.id}",
            headers=headers,
            json={"title": "Merchant Title", "brand": "Merchant Brand"},
        )
        assert response.status_code == 200, response.text

        row = (
            await db_session.execute(select(Product).where(Product.id == product.id))
        ).scalar_one()
        assert row.supplier_title == "Editable draft title"
        assert row.supplier_brand == "Original Brand"
        assert row.title == "Merchant Title"
        assert row.brand == "Merchant Brand"

    async def test_editing_description_never_changes_the_supplier_snapshot(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers, product = await _seed(client, db_session)

        response = await client.patch(
            f"{DRAFTS_URL}/{product.id}",
            headers=headers,
            json={"description": "<p>Merchant description</p>"},
        )
        assert response.status_code == 200, response.text

        row = (
            await db_session.execute(select(Product).where(Product.id == product.id))
        ).scalar_one()
        assert row.supplier_description == "<p>Original</p>"
        assert "Merchant description" in (row.description or "")


class TestTenantAndRoleIsolation:
    async def test_another_tenants_draft_is_404_not_409(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        _, product = await _seed(client, db_session)
        other = await register(client)
        other_headers = auth_header(other)

        response = await client.patch(
            f"{DRAFTS_URL}/{product.id}",
            headers=other_headers,
            json={"title": "Hijacked"},
        )
        assert response.status_code == 404, response.text

    async def test_a_stale_save_against_another_tenants_draft_is_still_404(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """The publish-gate lookup must not leak existence of a foreign
        draft ahead of the ordinary tenant-scoped 404."""
        _, product = await _seed(client, db_session)
        other = await register(client)
        other_headers = auth_header(other)

        response = await client.patch(
            f"{DRAFTS_URL}/{product.id}",
            headers=other_headers,
            json={"title": "Hijacked", "expectedUpdatedAt": "2020-01-01T00:00:00Z"},
        )
        assert response.status_code == 404, response.text

    async def test_non_admin_cannot_save_a_draft(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        _, product = await _seed(client, db_session)
        token = create_access_token(
            user_id=uuid.uuid4(), tenant_id=product.tenant_id, roles=("viewer",)
        )
        viewer_headers = {"Authorization": f"Bearer {token.token}"}

        response = await client.patch(
            f"{DRAFTS_URL}/{product.id}", headers=viewer_headers, json={"title": "x"}
        )
        assert response.status_code == 403, response.text
