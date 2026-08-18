"""M2B — the rich-text description write path.

M2A proved the draft editor's compare-and-swap. M2B changes what the editor
*sends*: a WYSIWYG surface re-serialises markup on every keystroke, so the
bytes arriving here are canonical-for-TipTap, not canonical-for-the-database.
Two guarantees follow, and both are tested below:

1. **Sanitization runs before the no-op comparison.** Until M2B the order was
   reversed — the raw submission was diffed against the stored (already
   sanitized) value, so `<br />` vs `<br>`, a stripped `class`, or a
   rewritten `rel` each read as a change and wrote an identical value. With
   autosave firing every 1.8s that is a continuous stream of pointless
   `UPDATE`s, each one moving `updated_at` and invalidating every other
   open tab's version token for a save that changed nothing.
2. **Merchant HTML is untrusted.** The editor's schema cannot express a
   script, but the editor is not the only client — the API is. Everything
   here goes through `PATCH /drafts/{id}` with a normal token, the same way
   a hand-rolled `curl` would.

The size limit (`DESCRIPTION_MAX_LENGTH`) is tested here too rather than as a
schema unit test, because what matters is the *response* a merchant gets, not
that a Pydantic field carries a constraint.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import set_tenant_id
from app.models.product import Product, ProductSource, ProductStatus
from app.schemas.product import DESCRIPTION_MAX_LENGTH
from tests.integration.test_products import auth_header, register

pytestmark = pytest.mark.integration

DRAFTS_URL = "/api/v1/drafts"

#: What the row holds before each test. Written in the sanitizer's own output
#: form on purpose: a fixture that seeded non-canonical markup would make the
#: first save look like a real change and hide the very behaviour under test.
STORED_DESCRIPTION = "<p>Original</p>"


async def _seed(
    client: AsyncClient, db_session: AsyncSession, *, description: str = STORED_DESCRIPTION
) -> tuple[dict[str, str], Product]:
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
        description=description,
        supplier_description=description,
        supplier_title="Editable draft title",
    )
    db_session.add(product)
    await db_session.flush()
    return headers, product


async def _load(
    client: AsyncClient, product: Product, headers: dict[str, str]
) -> dict[str, object]:
    response = await client.get(f"{DRAFTS_URL}/{product.id}", headers=headers)
    assert response.status_code == 200, response.text
    body: dict[str, object] = response.json()
    return body


async def _stored_description(db_session: AsyncSession, product: Product) -> str | None:
    row = (await db_session.execute(select(Product).where(Product.id == product.id))).scalar_one()
    await db_session.refresh(row)
    return row.description


class TestSanitizationRunsBeforeTheNoOpComparison:
    """The M2B ordering fix. Each case submits markup that is *textually*
    different from the stored value but *semantically* identical once
    sanitized, and asserts no write happened."""

    @pytest.mark.parametrize(
        ("submitted", "why"),
        [
            (
                '<p class="ProseMirror-trailing">Original</p>',
                "the editor adds presentational classes the sanitizer strips",
            ),
            (
                "<p>Original</p><script>alert(1)</script>",
                "content the sanitizer discards entirely",
            ),
            (
                "<p>Original</p><!-- editor bookkeeping -->",
                "comments are stripped",
            ),
        ],
        ids=["stripped-class", "stripped-script", "stripped-comment"],
    )
    async def test_markup_that_sanitizes_to_the_stored_value_does_not_move_updated_at(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        submitted: str,
        why: str,
    ) -> None:
        headers, product = await _seed(client, db_session)
        loaded = await _load(client, product, headers)

        response = await client.patch(
            f"{DRAFTS_URL}/{product.id}",
            headers=headers,
            json={"description": submitted, "expectedUpdatedAt": loaded["updatedAt"]},
        )
        assert response.status_code == 200, response.text
        assert response.json()["updatedAt"] == loaded["updatedAt"], why
        assert response.json()["description"] == STORED_DESCRIPTION

    async def test_a_self_closing_break_matching_the_stored_break_is_a_no_op(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """The single most likely false-positive in practice: the sanitizer
        emits `<br>`, ProseMirror re-serialises it as `<br />`, and every
        autosave of an untouched description would have written."""
        headers, product = await _seed(client, db_session, description="<p>Line<br>Break</p>")
        loaded = await _load(client, product, headers)

        response = await client.patch(
            f"{DRAFTS_URL}/{product.id}",
            headers=headers,
            json={
                "description": "<p>Line<br />Break</p>",
                "expectedUpdatedAt": loaded["updatedAt"],
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["updatedAt"] == loaded["updatedAt"]

    async def test_a_no_op_description_save_is_not_a_conflict_against_a_stale_token(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """The decisive test.

        Its siblings above cannot distinguish "no write happened" from "a
        write happened but this test's frozen transaction timestamp did not
        move" — inside one transaction Postgres's `now()` is constant, so an
        `UPDATE` can land without `updated_at` appearing to change.

        Deliberately staling the token removes that ambiguity: if the
        submission reaches the compare-and-swap it must 409, because the row
        has demonstrably moved on. A 200 is only reachable by the no-op
        filter short-circuiting first — which it can only do if sanitization
        ran *before* the comparison. Against the pre-M2B ordering this
        returns 409.
        """
        headers, product = await _seed(client, db_session)
        loaded = await _load(client, product, headers)

        concurrent_write_at = product.updated_at + timedelta(seconds=5)
        await db_session.execute(
            update(Product).where(Product.id == product.id).values(updated_at=concurrent_write_at)
        )
        await db_session.flush()

        response = await client.patch(
            f"{DRAFTS_URL}/{product.id}",
            headers=headers,
            json={
                "description": '<p class="ProseMirror-trailing">Original</p>',
                "expectedUpdatedAt": loaded["updatedAt"],
            },
        )
        assert response.status_code == 200, response.text

    async def test_a_genuinely_changed_description_still_writes(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """The control. Without it every test above would also pass on an
        implementation that simply refused to save descriptions."""
        headers, product = await _seed(client, db_session)
        loaded = await _load(client, product, headers)

        response = await client.patch(
            f"{DRAFTS_URL}/{product.id}",
            headers=headers,
            json={
                "description": "<p>Genuinely rewritten</p>",
                "expectedUpdatedAt": loaded["updatedAt"],
            },
        )
        assert response.status_code == 200, response.text
        assert await _stored_description(db_session, product) == "<p>Genuinely rewritten</p>"

    async def test_a_no_op_description_does_not_block_a_real_change_in_the_same_save(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """The filter is per-field, not per-request: an unchanged description
        travelling alongside a changed title must not suppress the title."""
        headers, product = await _seed(client, db_session)
        loaded = await _load(client, product, headers)

        response = await client.patch(
            f"{DRAFTS_URL}/{product.id}",
            headers=headers,
            json={
                "title": "New Title",
                "description": '<p class="x">Original</p>',
                "expectedUpdatedAt": loaded["updatedAt"],
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["title"] == "New Title"
        assert await _stored_description(db_session, product) == STORED_DESCRIPTION


class TestMerchantHtmlIsSanitizedOnTheDraftEndpoint:
    """The editor's schema cannot produce these; the API can still receive
    them. Asserted against the *database row*, not the response body, so a
    hypothetical response-only filter could not make these pass."""

    @pytest.mark.parametrize(
        ("payload", "forbidden"),
        [
            ("<p>Buy</p><script>steal(document.cookie)</script>", ["script", "steal"]),
            ('<p>Buy</p><iframe src="https://evil.example/x"></iframe>', ["iframe", "evil"]),
            ("<p>Buy</p><style>body{display:none}</style>", ["<style", "display:none"]),
            (
                '<p>Buy</p><img src="https://cdn.example/a.jpg" onerror="steal()">',
                ["onerror", "steal"],
            ),
            ('<a href="javascript:alert(1)">Buy</a>', ["javascript:"]),
            ('<a href="JaVaScRiPt:alert(1)">Buy</a>', ["javascript:", "JaVaScRiPt"]),
            ('<a href="java&#115;cript:alert(1)">Buy</a>', ["javascript:", "&#115;"]),
            ('<a href="&#106;avascript:alert(1)">Buy</a>', ["javascript:", "&#106;"]),
            ('<a href="vbscript:msgbox(1)">Buy</a>', ["vbscript:"]),
            ('<svg onload="alert(1)"><p>Buy</p></svg>', ["svg", "onload"]),
            ('<p onclick="steal()">Buy</p>', ["onclick", "steal"]),
            ('<form action="https://evil.example">Buy</form>', ["<form", "evil"]),
            (
                '<p>Buy</p><img src="data:text/html;base64,PHN2Zz4=" alt="x">',
                ["data:", "base64"],
            ),
            ('<p style="background:url(javascript:alert(1))">Buy</p>', ["style", "javascript"]),
        ],
        ids=[
            "script",
            "iframe",
            "style-block",
            "onerror",
            "javascript-href",
            "mixed-case-javascript-href",
            "entity-encoded-javascript-href",
            "numeric-entity-javascript-href",
            "vbscript-href",
            "svg-onload",
            "onclick",
            "form",
            "data-uri-image",
            "style-attribute",
        ],
    )
    async def test_a_hostile_payload_never_reaches_storage(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        payload: str,
        forbidden: list[str],
    ) -> None:
        headers, product = await _seed(client, db_session)
        loaded = await _load(client, product, headers)

        response = await client.patch(
            f"{DRAFTS_URL}/{product.id}",
            headers=headers,
            json={"description": payload, "expectedUpdatedAt": loaded["updatedAt"]},
        )
        assert response.status_code == 200, response.text

        stored = await _stored_description(db_session, product) or ""
        for token in forbidden:
            assert token.lower() not in stored.lower(), f"{token!r} survived in {stored!r}"
        # The visible text is kept -- proving the payload was cleaned rather
        # than the whole field being dropped, which would pass the loop above
        # for the wrong reason.
        assert "Buy" in stored

    async def test_sanitized_storage_is_stable_across_a_resave(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Sanitize-then-store is only safe if it converges. If a second pass
        over already-clean output changed anything, every reload-and-save
        cycle would rewrite the row and the no-op filter would never fire."""
        headers, product = await _seed(client, db_session)
        loaded = await _load(client, product, headers)

        first = await client.patch(
            f"{DRAFTS_URL}/{product.id}",
            headers=headers,
            json={
                "description": '<p class="x">Keep <strong>this</strong></p><script>no()</script>',
                "expectedUpdatedAt": loaded["updatedAt"],
            },
        )
        assert first.status_code == 200, first.text
        canonical = first.json()["description"]

        second = await client.patch(
            f"{DRAFTS_URL}/{product.id}",
            headers=headers,
            json={"description": canonical, "expectedUpdatedAt": first.json()["updatedAt"]},
        )
        assert second.status_code == 200, second.text
        assert second.json()["description"] == canonical
        assert second.json()["updatedAt"] == first.json()["updatedAt"]


class TestImportedImagesSurviveMerchantEditing:
    """Supplier descriptions are mostly images. M2B adds no way to insert one,
    but it must not destroy the ones already there — round-tripping a draft
    through the editor silently emptying its gallery would be worse than the
    raw-HTML textarea it replaces."""

    IMPORTED = (
        "<p>Specs</p>"
        '<img src="https://ae01.alicdn.com/kf/one.jpg" alt="Front view">'
        '<img src="http://ae01.alicdn.com/kf/two.jpg" title="Back view">'
    )

    async def test_supplier_images_survive_a_merchant_save(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers, product = await _seed(client, db_session, description=self.IMPORTED)
        loaded = await _load(client, product, headers)

        response = await client.patch(
            f"{DRAFTS_URL}/{product.id}",
            headers=headers,
            json={
                "description": f"<h2>Our take</h2>{self.IMPORTED}",
                "expectedUpdatedAt": loaded["updatedAt"],
            },
        )
        assert response.status_code == 200, response.text

        stored = await _stored_description(db_session, product) or ""
        assert "https://ae01.alicdn.com/kf/one.jpg" in stored
        assert "http://ae01.alicdn.com/kf/two.jpg" in stored
        assert 'alt="Front view"' in stored
        assert "<h2>Our take</h2>" in stored

    async def test_resaving_an_image_only_description_unchanged_is_a_no_op(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers, product = await _seed(client, db_session, description=self.IMPORTED)
        loaded = await _load(client, product, headers)

        response = await client.patch(
            f"{DRAFTS_URL}/{product.id}",
            headers=headers,
            json={"description": self.IMPORTED, "expectedUpdatedAt": loaded["updatedAt"]},
        )
        assert response.status_code == 200, response.text
        assert response.json()["updatedAt"] == loaded["updatedAt"]

    async def test_an_unsafe_image_src_is_dropped_while_safe_ones_remain(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers, product = await _seed(client, db_session, description=self.IMPORTED)
        loaded = await _load(client, product, headers)

        response = await client.patch(
            f"{DRAFTS_URL}/{product.id}",
            headers=headers,
            json={
                "description": f'{self.IMPORTED}<img src="javascript:alert(1)" alt="bad">',
                "expectedUpdatedAt": loaded["updatedAt"],
            },
        )
        assert response.status_code == 200, response.text

        stored = await _stored_description(db_session, product) or ""
        assert "javascript:" not in stored.lower()
        assert "https://ae01.alicdn.com/kf/one.jpg" in stored


class TestDescriptionSizeLimit:
    """A limit that only exists as a Pydantic constraint is untested; what
    matters is that the merchant gets a 422 instead of a truncated listing."""

    @staticmethod
    def _description_of_length(total: int) -> str:
        body = "a" * (total - len("<p></p>"))
        html = f"<p>{body}</p>"
        assert len(html) == total
        return html

    async def test_a_description_at_the_limit_is_accepted(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers, product = await _seed(client, db_session)
        loaded = await _load(client, product, headers)

        response = await client.patch(
            f"{DRAFTS_URL}/{product.id}",
            headers=headers,
            json={
                "description": self._description_of_length(DESCRIPTION_MAX_LENGTH),
                "expectedUpdatedAt": loaded["updatedAt"],
            },
        )
        assert response.status_code == 200, response.text

    async def test_a_description_over_the_limit_is_rejected(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers, product = await _seed(client, db_session)
        loaded = await _load(client, product, headers)

        response = await client.patch(
            f"{DRAFTS_URL}/{product.id}",
            headers=headers,
            json={
                "description": self._description_of_length(DESCRIPTION_MAX_LENGTH + 1),
                "expectedUpdatedAt": loaded["updatedAt"],
            },
        )
        assert response.status_code == 422, response.text
        assert await _stored_description(db_session, product) == STORED_DESCRIPTION

    async def test_the_limit_is_measured_before_sanitization_not_after(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """An over-limit payload made almost entirely of markup the sanitizer
        would strip is still rejected. Measuring the cleaned output instead
        would mean parsing arbitrarily large hostile input first — the check
        exists partly to avoid doing that work."""
        headers, product = await _seed(client, db_session)
        loaded = await _load(client, product, headers)

        padding = "<script>x</script>" * (DESCRIPTION_MAX_LENGTH // len("<script>x</script>") + 1)

        response = await client.patch(
            f"{DRAFTS_URL}/{product.id}",
            headers=headers,
            json={"description": f"<p>hi</p>{padding}", "expectedUpdatedAt": loaded["updatedAt"]},
        )
        assert response.status_code == 422, response.text


class TestTenantIsolationOnTheDescriptionPath:
    async def test_another_tenants_draft_description_cannot_be_edited(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers, product = await _seed(client, db_session)
        loaded = await _load(client, product, headers)
        intruder = auth_header(await register(client))

        response = await client.patch(
            f"{DRAFTS_URL}/{product.id}",
            headers=intruder,
            json={"description": "<p>Hijacked</p>", "expectedUpdatedAt": loaded["updatedAt"]},
        )
        assert response.status_code == 404, response.text
        assert await _stored_description(db_session, product) == STORED_DESCRIPTION


class TestSupplierSnapshotIsUntouchedByRichTextEditing:
    async def test_a_rich_text_save_never_rewrites_the_supplier_description(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers, product = await _seed(client, db_session)
        loaded = await _load(client, product, headers)

        response = await client.patch(
            f"{DRAFTS_URL}/{product.id}",
            headers=headers,
            json={
                "description": "<h2>Rewritten</h2><ul><li><p>One</p></li></ul>",
                "expectedUpdatedAt": loaded["updatedAt"],
            },
        )
        assert response.status_code == 200, response.text

        row = (
            await db_session.execute(select(Product).where(Product.id == product.id))
        ).scalar_one()
        await db_session.refresh(row)
        assert row.supplier_description == STORED_DESCRIPTION
        assert row.description == "<h2>Rewritten</h2><ul><li><p>One</p></li></ul>"
