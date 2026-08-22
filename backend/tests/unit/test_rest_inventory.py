"""The REST inventory is two files, and they must not be allowed to disagree.

A machine-readable inventory that drifts from the human-readable one is worse
than having only one, because a later phase reads whichever it happens to open
and believes it. These tests are the thing that makes the pair safe to keep.

They also assert the *content* rules the migration depends on: every discovered
call is present, ids are unique, phases are real, and a proposed GraphQL
replacement is never quietly marked verified without having been checked against
the official 2026-07 reference.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.unit

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_REPO_ROOT = _BACKEND_ROOT.parent
_DOCS = _REPO_ROOT / "docs" / "shopify-graphql"
_JSON_PATH = _DOCS / "rest-inventory.json"
_MARKDOWN_PATH = _DOCS / "REST_INVENTORY.md"

VALID_PHASES = {"GQL-2", "GQL-3", "GQL-4", "GQL-5", "GQL-6", "none"}

#: Phases whose migration has actually landed on this branch. A row may only
#: claim `removed` or `migrated` if its phase is here — otherwise a future phase
#: could mark itself finished in the document before the code changed.
SHIPPED_PHASES = {"GQL-2"}

#: `present`  — still called from production code.
#: `retained` — deliberately kept forever (OAuth).
#: `migrated` — the code still exists but has no production caller.
#: `removed`  — the call site is gone from the codebase.
VALID_REMOVAL = {"present", "retained", "migrated", "removed"}
VALID_VERIFICATION = {"verified", "unverified", "not-applicable"}
VALID_RISK = {"low", "medium", "high"}
REQUIRED_FIELDS = (
    "id",
    "method",
    "path",
    "sourceFile",
    "caller",
    "purpose",
    "operation",
    "scopes",
    "authority",
    "writesLocally",
    "shopifySideEffect",
    "idempotency",
    "retry",
    "pagination",
    "tests",
    "proposedGraphql",
    "targetPhase",
    "schemaVerification",
    "risk",
    "productionUsage",
    "removalStatus",
    "permittedInNewPublicApp",
)

#: Every Shopify Admin call the GQL-1 sweep found, by id. Written out here
#: rather than derived from the JSON so the test genuinely pins the discovery:
#: deleting a row from the inventory to make a later phase look finished fails
#: here instead of passing quietly.
DISCOVERED_CALLS = {
    "REST-001": ("GET", "backend/app/integrations/shopify/sync.py"),
    "REST-002": ("POST", "backend/app/integrations/shopify/sync.py"),
    "REST-003": ("PUT", "backend/app/integrations/shopify/sync.py"),
    "REST-004": ("GET", "backend/app/integrations/shopify/sync.py"),
    "REST-005": ("POST", "backend/app/integrations/shopify/sync.py"),
    "REST-006": ("PUT", "backend/app/integrations/shopify/sync.py"),
    "REST-007": ("GET", "backend/app/integrations/shopify/sync.py"),
    "REST-008": ("GET", "backend/app/integrations/shopify/service.py"),
    "REST-009": ("POST", "backend/app/integrations/shopify/service.py"),
    "REST-010": ("GET", "backend/app/integrations/shopify/client.py"),
    "REST-011": ("DELETE", "backend/app/integrations/shopify/client.py"),
    "REST-012": ("DELETE", "backend/app/integrations/shopify/client.py"),
    "OAUTH-001": ("GET", "backend/app/integrations/shopify/auth.py"),
    "OAUTH-002": ("POST", "backend/app/integrations/shopify/client.py"),
    "GQL-000": ("POST", "backend/app/integrations/shopify/client.py"),
}


def _load() -> list[dict[str, Any]]:
    payload = json.loads(_JSON_PATH.read_text(encoding="utf-8"))
    calls = payload["calls"]
    assert isinstance(calls, list)
    return calls


def _markdown_ids() -> list[str]:
    """Ids as they appear in the Markdown detail headings, in order."""
    text = _MARKDOWN_PATH.read_text(encoding="utf-8")
    return re.findall(r"^### ([A-Z]+-\d+) — ", text, flags=re.MULTILINE)


class TestBothFilesExist:
    def test_the_inventory_is_published_in_both_forms(self) -> None:
        assert _JSON_PATH.is_file(), "rest-inventory.json is missing"
        assert _MARKDOWN_PATH.is_file(), "REST_INVENTORY.md is missing"


class TestNoDrift:
    def test_the_two_files_describe_the_same_calls(self) -> None:
        json_ids = [call["id"] for call in _load()]
        assert _markdown_ids() == json_ids, (
            "REST_INVENTORY.md and rest-inventory.json list different calls, "
            "or list them in a different order"
        )

    def test_ids_are_unique(self) -> None:
        ids = [call["id"] for call in _load()]
        duplicates = {i for i in ids if ids.count(i) > 1}
        assert not duplicates, f"duplicate inventory ids: {sorted(duplicates)}"

    def test_every_discovered_call_is_represented(self) -> None:
        """The sweep's result, pinned.

        A call disappearing from the inventory is exactly how a REST endpoint
        survives to submission unnoticed, so removing one has to be a deliberate
        edit here as well.
        """
        by_id = {call["id"]: call for call in _load()}
        assert set(by_id) == set(DISCOVERED_CALLS), (
            f"inventory does not match the GQL-1 sweep; "
            f"missing={sorted(set(DISCOVERED_CALLS) - set(by_id))} "
            f"unexpected={sorted(set(by_id) - set(DISCOVERED_CALLS))}"
        )
        for identifier, (method, source) in DISCOVERED_CALLS.items():
            assert by_id[identifier]["method"] == method, identifier
            assert by_id[identifier]["sourceFile"] == source, identifier

    def test_each_markdown_entry_states_its_phase_and_verification(self) -> None:
        text = _MARKDOWN_PATH.read_text(encoding="utf-8")
        for call in _load():
            assert f"**Target phase**: {call['targetPhase']}" in text, call["id"]
            assert f"**Schema verification**: `{call['schemaVerification']}`" in text, call["id"]


class TestRequiredFields:
    def test_every_call_carries_every_required_field(self) -> None:
        for call in _load():
            missing = [f for f in REQUIRED_FIELDS if f not in call]
            assert not missing, f"{call.get('id')} is missing {missing}"

    def test_no_required_field_is_blank(self) -> None:
        for call in _load():
            for field in REQUIRED_FIELDS:
                value = call[field]
                if isinstance(value, str):
                    assert value.strip(), f"{call['id']}.{field} is blank"

    def test_phase_values_are_valid(self) -> None:
        for call in _load():
            assert call["targetPhase"] in VALID_PHASES, (
                f"{call['id']} has phase {call['targetPhase']!r}; GQL-1 is not a "
                "migration target and later phases must exist in the roadmap"
            )

    def test_risk_and_verification_values_are_valid(self) -> None:
        for call in _load():
            assert call["schemaVerification"] in VALID_VERIFICATION, call["id"]
            assert call["risk"] in VALID_RISK, call["id"]


class TestMigrationHonesty:
    def test_unverified_replacements_are_marked_not_assumed(self) -> None:
        """Anything whose 2026-07 field was not read in the official reference
        during this phase says so. A replacement that merely looks plausible is
        the failure mode this column exists to prevent."""
        for call in _load():
            if call["schemaVerification"] == "verified":
                assert call["proposedGraphql"].strip(), call["id"]
                assert call["proposedGraphql"] != "unverified", call["id"]

    def test_every_versioned_rest_call_is_flagged_as_not_permitted(self) -> None:
        """Shopify: new public apps "must only use GraphQL". Every versioned
        Admin REST call is therefore a submission blocker until migrated."""
        for call in _load():
            if call["id"].startswith("REST-"):
                assert call["permittedInNewPublicApp"] is False, (
                    f"{call['id']} is a versioned Admin REST call and cannot be "
                    "permitted in a new public app"
                )
                assert call["targetPhase"] != "none", (
                    f"{call['id']} must be assigned a migration phase"
                )

    def test_oauth_endpoints_are_retained_and_unversioned(self) -> None:
        for call in _load():
            if call["id"].startswith("OAUTH-"):
                assert call["permittedInNewPublicApp"] is True, call["id"]
                assert "/admin/api/" not in call["path"], (
                    f"{call['id']} is recorded as OAuth but uses a versioned Admin path"
                )

    def test_removal_status_values_are_valid(self) -> None:
        for call in _load():
            assert call["removalStatus"] in VALID_REMOVAL, (
                f"{call['id']} claims removalStatus={call['removalStatus']!r}"
            )

    def test_nothing_is_marked_removed_before_its_phase_ships(self) -> None:
        """A phase cannot declare itself finished in the document first.

        Marking a row migrated ahead of the code is the exact failure this
        inventory exists to prevent — a later phase reads it, believes the call
        is gone, and skips it.
        """
        for call in _load():
            if call["removalStatus"] in {"migrated", "removed"}:
                assert call["targetPhase"] in SHIPPED_PHASES, (
                    f"{call['id']} claims removalStatus={call['removalStatus']!r} but its "
                    f"phase {call['targetPhase']} has not shipped on this branch"
                )

    def test_a_shipped_phase_has_actually_migrated_every_call_it_owns(self) -> None:
        """The other direction: a shipped phase may not leave a row behind."""
        for call in _load():
            if call["targetPhase"] in SHIPPED_PHASES:
                assert call["removalStatus"] in {"migrated", "removed"}, (
                    f"{call['id']} belongs to shipped phase {call['targetPhase']} but is "
                    f"still marked {call['removalStatus']!r}"
                )


class TestPinnedVersion:
    def test_the_inventory_names_the_graphql_version_it_was_written_against(self) -> None:
        payload = json.loads(_JSON_PATH.read_text(encoding="utf-8"))
        assert payload["graphqlApiVersion"] == "2026-07"
        assert re.fullmatch(r"[0-9a-f]{40}", payload["generatedForCommit"])


# ---------------------------------------------------------------------------
# The inventory can only be trusted if the code agrees with it.
# ---------------------------------------------------------------------------

_APP_ROOT = _BACKEND_ROOT / "app"

#: What "gone" means for each migrated call, stated as something checkable.
#: A status column is only worth having if the document cannot claim a call is
#: migrated while the call site is still sitting in the codebase — so each entry
#: is (needle, modules the needle may still legitimately appear in).
MIGRATED_SIGNATURES: dict[str, tuple[str, frozenset[str]]] = {
    # REST-008 and REST-009 shared one path and one caller, so they share one
    # needle. The path survives in client.py as REST-010, which is GQL-6's to
    # remove — that module is the only place it may still appear.
    "REST-008": ('"/webhooks.json"', frozenset({"integrations/shopify/client.py"})),
    "REST-009": ('"/webhooks.json"', frozenset({"integrations/shopify/client.py"})),
    # The method survives for an existing test but must have no caller.
    "GQL-000": ("fetch_shop_currency_code", frozenset({"integrations/shopify/client.py"})),
}


def _app_modules() -> list[Path]:
    return sorted(_APP_ROOT.rglob("*.py"))


def _occurrences(needle: str) -> list[str]:
    """Repo-relative modules under ``app/`` containing ``needle``."""
    found = []
    for module in _app_modules():
        if needle in module.read_text(encoding="utf-8"):
            found.append(module.relative_to(_APP_ROOT).as_posix())
    return found


class TestTheCodeAgreesWithTheInventory:
    """Drift guard. Without this, the status column is a promise, not a fact."""

    def test_every_test_path_the_inventory_cites_actually_exists(self) -> None:
        """A cited test that does not exist is a coverage claim, not coverage."""
        for call in _load():
            for cited in call["tests"]:
                assert (_REPO_ROOT / cited).is_file(), f"{call['id']} cites missing {cited}"

    def test_each_drift_needle_still_matches_where_it_should(self) -> None:
        """A needle that matches nothing is a test that can never fail.

        This is the guard on the guard: it pins where each string legitimately
        still lives, so a typo, a reformat or a rename turns into a failure here
        rather than into a drift check that silently passes forever.
        """
        for identifier, (needle, permitted) in MIGRATED_SIGNATURES.items():
            assert _occurrences(needle) == sorted(permitted), (
                f"{identifier}: {needle!r} no longer matches {sorted(permitted)} — "
                "update the signature rather than leaving a check that cannot fire"
            )

    def test_every_call_marked_migrated_really_has_no_production_call_site(self) -> None:
        by_id = {call["id"]: call for call in _load()}
        for identifier, (needle, permitted) in MIGRATED_SIGNATURES.items():
            assert by_id[identifier]["removalStatus"] in {"migrated", "removed"}, identifier
            stray = set(_occurrences(needle)) - set(permitted)
            assert not stray, (
                f"{identifier} is marked {by_id[identifier]['removalStatus']!r} but "
                f"{needle} still appears in {sorted(stray)}"
            )

    def test_the_legacy_rest_currency_read_has_no_caller_at_all(self) -> None:
        """The method is retained for its existing test, not for use.

        Retaining dead code is a deliberate trade — the phase brief forbids
        weakening an existing test to make a migration look tidier — but a
        retained method with no guard is one autocomplete away from being used
        again, so the caller count is pinned at zero here.
        """
        client = (_APP_ROOT / "integrations/shopify/client.py").read_text(encoding="utf-8")
        assert "async def fetch_shop_currency_code" in client, (
            "the method was deleted; tests/unit/test_m24a_currency_fx.py drives it"
        )
        callers = [
            module
            for module in _occurrences("fetch_shop_currency_code")
            if module != "integrations/shopify/client.py"
        ]
        assert callers == [], f"the legacy currency read is called again from {callers}"

    def test_the_legacy_graphql_method_is_only_used_by_its_own_module(self) -> None:
        callers = [
            module
            for module in _occurrences("await self.graphql(")
            if module != "integrations/shopify/client.py"
        ]
        assert callers == [], f"ShopifyClient.graphql is being used again from {callers}"

    def test_webhook_registration_no_longer_touches_rest(self) -> None:
        service = (_APP_ROOT / "integrations/shopify/service.py").read_text(encoding="utf-8")
        assert "webhooks.json" not in service
        assert "WebhookReconciler" in service, "the GraphQL replacement is wired in"

    def test_the_replacement_operations_exist_and_are_used(self) -> None:
        operations = (_APP_ROOT / "integrations/shopify/graphql_operations.py").read_text(
            encoding="utf-8"
        )
        for symbol in (
            "SHOP_AUTHORITY_QUERY",
            "WEBHOOK_SUBSCRIPTIONS_QUERY",
            "WEBHOOK_SUBSCRIPTION_CREATE_MUTATION",
        ):
            assert symbol in operations, symbol
        service = (_APP_ROOT / "integrations/shopify/service.py").read_text(encoding="utf-8")
        assert "fetch_shop_authority" in service

    def test_the_webhook_documents_do_not_select_deprecated_fields(self) -> None:
        """2026-07 deprecates `callbackUrl` and the `endpoint` union."""
        operations = (_APP_ROOT / "integrations/shopify/graphql_operations.py").read_text(
            encoding="utf-8"
        )
        query_start = operations.index("WEBHOOK_SUBSCRIPTIONS_QUERY")
        query_end = operations.index("WEBHOOK_PAGE_SIZE")
        documents = operations[query_start:query_end]
        assert "callbackUrl" not in documents
        assert "endpoint {" not in documents

    def test_no_app_scoped_toml_subscriptions_were_introduced(self) -> None:
        """Mixed mode delivers every event twice and cannot be detected.

        `webhookSubscriptions` returns only shop-scoped subscriptions, so if a
        `shopify.app.toml` ever declares the same topics while per-shop ones
        still exist, nothing in this codebase could notice.
        """
        candidates = [
            _REPO_ROOT / "shopify.app.toml",
            _BACKEND_ROOT / "shopify.app.toml",
            _REPO_ROOT / "frontend" / "shopify.app.toml",
        ]
        present = [path for path in candidates if path.exists()]
        assert not present, (
            f"{present} appeared while per-shop subscriptions are still created; "
            "running both modes for one topic duplicates every event, and "
            "webhookSubscriptions cannot see the app-scoped half"
        )
