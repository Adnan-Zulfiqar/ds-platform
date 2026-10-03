"""EBAY-C3a parsers and aspect rules — no network, no database."""

from __future__ import annotations

import pytest

from app.core.exceptions import ValidationError
from app.integrations.ebay.exceptions import EbaySellerApiUnavailableError
from app.integrations.ebay.product_details import missing_required, normalise_aspects
from app.integrations.ebay.taxonomy import EbayAspect, parse_aspects, parse_suggestions

pytestmark = pytest.mark.unit


def test_suggestions_skip_entries_without_an_id_and_build_a_root_first_path() -> None:
    parsed = parse_suggestions(
        {
            "categorySuggestions": [
                {"category": {"categoryName": "No id"}},
                {
                    "category": {"categoryId": "1", "categoryName": "Leaf"},
                    "categoryTreeNodeAncestors": [
                        {"categoryName": "Parent"},
                        {"categoryName": "Root"},
                    ],
                },
            ]
        }
    )
    assert [(s.category_id, s.path) for s in parsed] == [("1", "Root > Parent > Leaf")]


def test_aspects_put_required_first_and_read_the_constraint() -> None:
    parsed = parse_aspects(
        {
            "aspects": [
                {
                    "localizedAspectName": "Colour",
                    "aspectConstraint": {"aspectMode": "SELECTION_ONLY"},
                },
                {
                    "localizedAspectName": "Brand",
                    "aspectConstraint": {
                        "aspectRequired": True,
                        "itemToAspectCardinality": "MULTI",
                    },
                    "aspectValues": [{"localizedValue": "Acme"}, {}],
                },
            ]
        }
    )
    assert [a.name for a in parsed] == ["Brand", "Colour"]
    assert parsed[0].required and parsed[0].multiple and parsed[0].values == ("Acme",)
    assert parsed[1].selection_only and not parsed[1].required


@pytest.mark.parametrize("payload", [None, [], "text"])
def test_a_malformed_body_is_an_upstream_error(payload: object) -> None:
    with pytest.raises(EbaySellerApiUnavailableError):
        parse_suggestions(payload)
    with pytest.raises(EbaySellerApiUnavailableError):
        parse_aspects(payload)


def test_normalise_trims_and_drops_empty_values() -> None:
    assert normalise_aspects(
        {" Brand ": [" Acme "], "Empty": ["", " "], "": ["x"], "One": "solo"}
    ) == {
        "Brand": ["Acme"],
        "One": ["solo"],
    }


@pytest.mark.parametrize(
    "aspects",
    [
        {f"A{i}": ["v"] for i in range(46)},
        {"x" * 41: ["v"]},
        {"Brand": ["v" * 66]},
        {"Brand": [str(i) for i in range(31)]},
    ],
)
def test_normalise_refuses_rather_than_truncates(aspects: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        normalise_aspects(aspects)  # type: ignore[arg-type]  # deliberately wrong shapes


def test_missing_required_is_case_insensitive_and_ignores_optional() -> None:
    category = (
        EbayAspect(name="Brand", required=True, selection_only=False, multiple=False, values=()),
        EbayAspect(name="Type", required=True, selection_only=False, multiple=False, values=()),
        EbayAspect(name="Colour", required=False, selection_only=False, multiple=False, values=()),
    )
    assert missing_required({"brand": ["Acme"]}, category) == ("Type",)
