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

    def test_nothing_is_marked_removed_before_its_phase_ships(self) -> None:
        """GQL-1 migrates nothing, so no call may claim to be gone yet."""
        for call in _load():
            assert call["removalStatus"] in {"present", "retained"}, (
                f"{call['id']} claims removalStatus={call['removalStatus']!r}, but "
                "GQL-1 removes no REST call"
            )


class TestPinnedVersion:
    def test_the_inventory_names_the_graphql_version_it_was_written_against(self) -> None:
        payload = json.loads(_JSON_PATH.read_text(encoding="utf-8"))
        assert payload["graphqlApiVersion"] == "2026-07"
        assert re.fullmatch(r"[0-9a-f]{40}", payload["generatedForCommit"])
