"""EBAY-C0 — the contract that stops eBay data shipping without an eraser.

The previous guard was a regex over column names. It catches ``ebay_user_id``
and misses everything that matters: an identifier inside a ``JSONB`` bag, an
encrypted column called ``credentials``, a field called ``external_reference``,
an audit row quoting a payload. A guard that looks thorough and is not is worse
than an honest one, because the next author trusts it.

So there are two mechanisms here, and only one of them is a guarantee:

* **``EBAY_STORAGE_DECLARATIONS`` — the contract.** A change that persists eBay
  personal data declares it and names the owner that erases it. The declaration
  and the owner must agree, and the tests below fail when they do not. This is
  the mechanism.
* **The column sweep — a backstop.** It catches the obvious spelling, and its
  limits are documented rather than papered over.

Today both agree on the same answer: nothing stores eBay personal data, so
there is nothing to declare and nothing to erase.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.integrations.ebay.deletion import (
    EBAY_STORAGE_DECLARATIONS,
    DeletionSubject,
    EbayAccountDeletionProcessor,
    EbayStorageDeclaration,
)

pytestmark = pytest.mark.unit

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_MODELS = _BACKEND_ROOT / "app" / "models"


class TestTheContractHolds:
    def test_every_declared_storage_has_an_eraser(self) -> None:
        """The release blocker.

        Declaring that eBay personal data is stored, without registering
        something that erases it, means the application cannot honour a deletion
        request it is legally obliged to honour.
        """
        unowned = EbayAccountDeletionProcessor.unowned_declarations()
        assert unowned == (), (
            f"{list(unowned)} declare eBay personal data with no registered eraser. "
            "Register an EbayDataOwner in app/integrations/ebay/deletion.py."
        )

    def test_every_owner_corresponds_to_a_declaration(self) -> None:
        """An eraser for storage nobody named is usually a leftover.

        A dead owner keeps looking like coverage while nothing maintains it.
        """
        undeclared = EbayAccountDeletionProcessor.undeclared_owner_names()
        assert undeclared == (), (
            f"{list(undeclared)} erase storage that is not declared. Add an "
            "EbayStorageDeclaration, or remove the owner if the storage is gone."
        )

    def test_todays_answer_is_nothing_stored_and_nothing_erased(self) -> None:
        """EBAY-C0's verified zero-match state, asserted from both sides."""
        assert EBAY_STORAGE_DECLARATIONS == ()
        assert EbayAccountDeletionProcessor.registered_owners() == ()

    def test_the_contract_actually_detects_a_missing_eraser(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The guard on the guard.

        A check that always passes is indistinguishable from no check. This
        declares storage with no owner and requires the failure to appear.
        """
        from app.integrations.ebay import deletion as module

        monkeypatch.setattr(
            module,
            "EBAY_STORAGE_DECLARATIONS",
            (
                EbayStorageDeclaration(
                    storage="app.models.hypothetical.EbayOrder",
                    owner_name="ebay_order_owner",
                    holds="eBay buyer id and shipping contact",
                ),
            ),
        )
        assert EbayAccountDeletionProcessor.unowned_declarations() == (
            "app.models.hypothetical.EbayOrder",
        )

    def test_a_declaration_and_owner_that_agree_pass(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """And the shape EBAY-C1 must produce, demonstrated once."""
        from app.integrations.ebay import deletion as module

        class Owner:
            name = "ebay_order_owner"

            async def erase(self, session: object, subject: DeletionSubject) -> int:
                return 0

        monkeypatch.setattr(
            module,
            "EBAY_STORAGE_DECLARATIONS",
            (
                EbayStorageDeclaration(
                    storage="app.models.hypothetical.EbayOrder",
                    owner_name="ebay_order_owner",
                    holds="eBay buyer id",
                ),
            ),
        )
        monkeypatch.setattr(module, "_OWNERS", (Owner(),))

        assert EbayAccountDeletionProcessor.unowned_declarations() == ()
        assert EbayAccountDeletionProcessor.undeclared_owner_names() == ()


class TestStorageSurfaceSweep:
    """The backstop, applied to every storage shape eBay data could hide in.

    Not a guarantee — see the module docstring — but it covers the spellings a
    reviewer would actually expect someone to use.
    """

    #: Column names that would plainly hold an eBay identifier.
    _NAME_PATTERN = re.compile(
        r"^\s*(ebay_[a-z_]*(user|buyer|seller|account|token|id)[a-z_]*|eias_token)\s*:",
        re.MULTILINE,
    )

    def test_no_model_declares_an_ebay_identifier_column(self) -> None:
        offenders = [
            module.name
            for module in sorted(_MODELS.glob("*.py"))
            if module.name != "ebay.py" and self._NAME_PATTERN.search(module.read_text("utf-8"))
        ]
        assert offenders == [], (
            f"{offenders} name eBay identifier columns. Declare the storage and "
            "register an eraser before this can ship."
        )

    @pytest.mark.parametrize(
        "surface",
        [
            "integration.py",
            "order.py",
            "product.py",
            "store.py",
            "shopify.py",
            "notification.py",
            "analytics.py",
            "automation.py",
            "inventory.py",
            "pricing.py",
        ],
    )
    def test_generic_storage_on_each_surface_holds_no_ebay_payload(self, surface: str) -> None:
        """JSON, Text and encrypted columns, inspected surface by surface.

        These are the places an identifier could hide without a telling column
        name, so each is listed explicitly and checked for eBay references
        rather than assumed clean.
        """
        source = (_MODELS / surface).read_text("utf-8")
        generic = re.findall(
            r"^\s*(\w+):\s*Mapped\[[^\]]*\]\s*=\s*mapped_column\(\s*(JSONB|Text|JSON)\b",
            source,
            re.MULTILINE,
        )
        for column, _kind in generic:
            # A generic column is fine; a generic column *documented* as holding
            # eBay data is a declaration that never got made.
            window = source[max(0, source.find(column) - 600) : source.find(column) + 200]
            assert "ebay" not in window.lower() or "eBay and" in window, (
                f"{surface}:{column} is a generic column whose surrounding "
                "documentation mentions eBay — declare it or explain it"
            )

    def test_the_encrypted_credential_columns_are_accounted_for(self) -> None:
        """Encrypted blobs are the classic blind spot.

        Every one that exists today belongs to AliExpress or Shopify, and both
        are named. If an eBay one appears, it must be declared — an encrypted
        token is still personal data.
        """
        encrypted: list[str] = []
        for module in sorted(_MODELS.glob("*.py")):
            for match in re.finditer(
                r"^\s*(\w*(?:encrypted|credential)\w*):\s*Mapped",
                module.read_text("utf-8"),
                re.MULTILINE,
            ):
                encrypted.append(f"{module.name}:{match.group(1)}")

        assert encrypted, "the sweep found no encrypted columns at all — it is not working"
        for entry in encrypted:
            assert "ebay" not in entry.lower(), (
                f"{entry} is an eBay credential column with no declaration"
            )

    def test_the_sweep_would_actually_catch_something(self, tmp_path: Path) -> None:
        """Proof the pattern is live rather than a typo that never matches."""
        sample = tmp_path / "hypothetical.py"
        sample.write_text(
            "class EbayOrder(Base):\n    ebay_buyer_id: Mapped[str] = mapped_column(String)\n",
            encoding="utf-8",
        )
        assert self._NAME_PATTERN.search(sample.read_text("utf-8")) is not None


class TestLimitationIsDocumented:
    def test_the_module_states_what_the_sweep_cannot_do(self) -> None:
        """An unstated limitation is a trap for whoever hits it next."""
        source = (_BACKEND_ROOT / "app" / "integrations" / "ebay" / "deletion.py").read_text(
            "utf-8"
        )
        assert "What automated discovery cannot do" in source
        assert "JSONB" in source
        assert "EBAY_STORAGE_DECLARATIONS" in source
