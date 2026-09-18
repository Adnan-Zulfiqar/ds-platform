"""Unit coverage for `app.services.optimization_quality` (Phase 9 stage 5).

Every rule in docs/PHASE_9_STAGE_5_PLAN.md §6-§7 is asserted here as an
exact integer, on fixtures built to land on the rule's boundary. Nothing
needs a database, a session, or a provider: the scorer is a pure function
over a `content` mapping and a transient `Product`.
"""

from __future__ import annotations

import ast
import inspect
from typing import Any

import pytest

from app.models.product import Product
from app.services import optimization_quality
from app.services.optimization_quality import (
    ORIGINAL_VERSION_NUMBER,
    QUALITY_SCORE_VERSION,
    QualityResult,
    score_version,
)

pytestmark = pytest.mark.unit


def _product(**overrides: object) -> Product:
    """A transient product with no merchant keywords unless overridden.

    Column defaults (`[]`) only apply at flush, so a bare `Product()` reads
    `None` for the JSONB lists — exactly the shape the scorer must tolerate.
    """
    product = Product(title="Fixture", brand=None)
    product.search_topics = None
    product.tags = None
    product.meta_keywords = None
    for key, value in overrides.items():
        setattr(product, key, value)
    return product


def _score(content: dict[str, Any] | None, **product_overrides: object) -> QualityResult:
    return score_version(content, _product(**product_overrides))


def _dims(result: QualityResult) -> dict[str, Any]:
    return result.as_content()["qualityBreakdown"]["dimensions"]


# -- Normalisation ----------------------------------------------------------------


class TestNormalisation:
    def test_html_is_stripped_and_whitespace_collapsed(self) -> None:
        result = _score({"title": "<p>a  b</p>\n<p>c</p>"})
        assert _dims(result)["title"]["length"] == len("a b c")

    @pytest.mark.parametrize("value", [None, 0, [], {}])
    def test_non_string_values_are_blank(self, value: object) -> None:
        result = _score({"title": value, "description": value})
        assert _dims(result)["title"]["length"] == 0
        assert _dims(result)["description"]["length"] == 0


# -- D1 -------------------------------------------------------------------------------


class TestTitleLength:
    @pytest.mark.parametrize(
        ("length", "points"),
        [
            (0, 0),
            (1, 6),
            (9, 6),
            (10, 12),
            (19, 12),
            (20, 25),
            (120, 25),
            (121, 15),
            (255, 15),
            (256, 0),
        ],
    )
    def test_every_tier_boundary(self, length: int, points: int) -> None:
        result = _score({"title": "x" * length})
        assert _dims(result)["title"] == {
            "points": points,
            "max": 25,
            "applicable": True,
            "length": length,
        }

    def test_whitespace_only_is_blank(self) -> None:
        assert _dims(_score({"title": "   \n\t "}))["title"]["points"] == 0

    def test_tags_are_stripped_before_measuring(self) -> None:
        # Nine visible characters wrapped in markup: tier 1-9, not 20-120.
        assert _dims(_score({"title": "<b>Phone Cas</b>"}))["title"]["points"] == 6


# -- D2 -------------------------------------------------------------------------------


class TestDescriptionLength:
    @pytest.mark.parametrize(
        ("length", "points"),
        [
            (0, 0),
            (1, 6),
            (49, 6),
            (50, 12),
            (149, 12),
            (150, 25),
            (2000, 25),
            (2001, 18),
            (5000, 18),
            (5001, 12),
        ],
    )
    def test_every_tier_boundary(self, length: int, points: int) -> None:
        result = _score({"description": "x" * length})
        assert _dims(result)["description"] == {
            "points": points,
            "max": 25,
            "applicable": True,
            "length": length,
        }

    def test_html_only_content_is_blank(self) -> None:
        assert _dims(_score({"description": "<p></p>"}))["description"]["points"] == 0

    def test_tag_characters_do_not_count_toward_length(self) -> None:
        # 49 characters of text inside enough markup to exceed 50 raw bytes.
        result = _score({"description": "<p><strong>" + "x" * 49 + "</strong></p>"})
        assert _dims(result)["description"] == {
            "points": 6,
            "max": 25,
            "applicable": True,
            "length": 49,
        }


# -- D3 -------------------------------------------------------------------------------


def _checks(result: QualityResult) -> dict[str, bool]:
    checks: dict[str, bool] = _dims(result)["repetition"]["checks"]
    return checks


class TestRepetition:
    def test_title_token_three_times_fails_d3a(self) -> None:
        result = _score({"title": "Case Case Case for Phone"})
        assert _checks(result)["titleNotStuffed"] is False

    def test_title_token_twice_passes_d3a(self) -> None:
        result = _score({"title": "Case Case for Phone"})
        assert _checks(result)["titleNotStuffed"] is True

    def test_short_tokens_are_ignored_by_d3a(self) -> None:
        result = _score({"title": "ab ab ab ab ab"})
        assert _checks(result)["titleNotStuffed"] is True

    def test_description_bigram_three_times_fails_d3b(self) -> None:
        result = _score({"description": "good fit good fit good fit"})
        assert _checks(result)["descriptionNotPhraseStuffed"] is False

    def test_description_bigram_twice_passes_d3b(self) -> None:
        result = _score({"description": "good fit good fit"})
        assert _checks(result)["descriptionNotPhraseStuffed"] is True

    def test_fewer_than_twenty_tokens_is_not_evaluable_for_d3c(self) -> None:
        # 19 tokens, one of them ten times: would be 52 % if evaluated.
        words = ["stuff"] * 10 + [f"w{i}" for i in range(9)]
        result = _score({"description": " ".join(words)})
        assert len(words) == 19
        assert _checks(result)["descriptionNotDominated"] is True

    def test_more_than_a_quarter_of_twenty_tokens_fails_d3c(self) -> None:
        words = ["stuff"] * 6 + [f"w{i}" for i in range(14)]
        result = _score({"description": " ".join(words)})
        assert len(words) == 20
        assert _checks(result)["descriptionNotDominated"] is False

    def test_exactly_a_quarter_passes_d3c(self) -> None:
        words = ["stuff"] * 5 + [f"w{i}" for i in range(15)]
        result = _score({"description": " ".join(words)})
        assert len(words) == 20
        assert _checks(result)["descriptionNotDominated"] is True

    def test_description_equal_to_title_fails_d3d(self) -> None:
        result = _score({"title": "Phone  Case", "description": "<p>phone case</p>"})
        assert _checks(result)["descriptionDistinctFromTitle"] is False

    def test_description_differing_by_one_character_passes_d3d(self) -> None:
        result = _score({"title": "Phone Case", "description": "Phone Cases"})
        assert _checks(result)["descriptionDistinctFromTitle"] is True

    def test_blank_description_fails_d3d_only(self) -> None:
        result = _score({"title": "Phone Case", "description": ""})
        assert _checks(result) == {
            "titleNotStuffed": True,
            "descriptionNotPhraseStuffed": True,
            "descriptionNotDominated": True,
            "descriptionDistinctFromTitle": False,
        }
        assert _dims(result)["repetition"]["points"] == 21

    def test_blank_title_still_passes_d3a(self) -> None:
        result = _score({"title": "", "description": "Something to say here."})
        assert _checks(result)["titleNotStuffed"] is True

    def test_points_are_the_sum_of_awarded_checks(self) -> None:
        # D3a fails (8 lost), D3d fails (4 lost): 25 - 12 = 13.
        result = _score({"title": "Case Case Case", "description": "Case Case Case"})
        assert _dims(result)["repetition"] == {
            "points": 13,
            "max": 25,
            "applicable": True,
            "checks": {
                "titleNotStuffed": False,
                "descriptionNotPhraseStuffed": True,
                "descriptionNotDominated": True,
                "descriptionDistinctFromTitle": False,
            },
        }


# -- D4 -------------------------------------------------------------------------------


_NOT_APPLICABLE = {
    "applicable": False,
    "points": 0,
    "max": 25,
    "matched": 0,
    "total": 0,
    "source": None,
    "keywords": [],
    "truncated": False,
}


class TestKeywordCoverage:
    def test_no_merchant_keywords_is_not_applicable_with_the_exact_shape(self) -> None:
        result = _score({"title": "Phone Case", "description": "A case."})
        assert _dims(result)["keywordCoverage"] == _NOT_APPLICABLE
        assert result.as_content()["qualityBreakdown"]["applicableMax"] == 75

    def test_applicable_shape_is_exact(self) -> None:
        result = _score(
            {"title": "Canvas Case", "description": "With camera protection."},
            search_topics=["canvas case", "camera protection"],
        )
        assert _dims(result)["keywordCoverage"] == {
            "applicable": True,
            "points": 25,
            "max": 25,
            "matched": 2,
            "total": 2,
            "source": "search_topics",
            "keywords": ["canvas case", "camera protection"],
            "truncated": False,
        }
        assert result.as_content()["qualityBreakdown"]["applicableMax"] == 100

    @pytest.mark.parametrize(
        ("text", "points", "matched"),
        [
            ("nothing relevant", 0, 0),
            ("a canvas case", 13, 1),
            ("canvas case camera protection", 25, 2),
        ],
    )
    def test_points_are_round_half_up_of_the_matched_fraction(
        self, text: str, points: int, matched: int
    ) -> None:
        result = _score(
            {"title": text, "description": ""},
            search_topics=["canvas case", "camera protection"],
        )
        coverage = _dims(result)["keywordCoverage"]
        assert (coverage["points"], coverage["matched"]) == (points, matched)

    def test_matching_is_case_insensitive(self) -> None:
        result = _score({"title": "Canvas Case"}, search_topics=["canvas case"])
        assert _dims(result)["keywordCoverage"]["matched"] == 1

    def test_matching_is_substring_not_word(self) -> None:
        result = _score({"title": "Phone cases"}, search_topics=["case"])
        assert _dims(result)["keywordCoverage"]["matched"] == 1

    def test_search_topics_beat_tags_and_meta_keywords(self) -> None:
        result = _score(
            {"title": "x"},
            search_topics=["topic"],
            tags=["tag"],
            meta_keywords="meta",
        )
        assert _dims(result)["keywordCoverage"]["source"] == "search_topics"
        assert _dims(result)["keywordCoverage"]["keywords"] == ["topic"]

    def test_tags_beat_meta_keywords(self) -> None:
        result = _score({"title": "x"}, search_topics=[], tags=["tag"], meta_keywords="meta")
        assert _dims(result)["keywordCoverage"]["source"] == "tags"
        assert _dims(result)["keywordCoverage"]["keywords"] == ["tag"]

    def test_meta_keywords_are_split_on_commas_and_stripped(self) -> None:
        result = _score({"title": "x"}, meta_keywords="  legacy , keywords,, ")
        assert _dims(result)["keywordCoverage"]["source"] == "meta_keywords"
        assert _dims(result)["keywordCoverage"]["keywords"] == ["legacy", "keywords"]

    def test_duplicates_collapse_case_insensitively_keeping_first(self) -> None:
        result = _score({"title": "x"}, search_topics=["Case", "case", "CASE", "cover"])
        assert _dims(result)["keywordCoverage"]["keywords"] == ["Case", "cover"]

    def test_non_string_entries_are_skipped(self) -> None:
        result = _score({"title": "x"}, search_topics=[None, 3, "real"])
        assert _dims(result)["keywordCoverage"]["keywords"] == ["real"]

    def test_blank_only_source_falls_through(self) -> None:
        result = _score({"title": "x"}, search_topics=["", "  "], tags=["tag"])
        assert _dims(result)["keywordCoverage"]["source"] == "tags"

    def test_sixty_terms_are_capped_at_fifty_and_marked_truncated(self) -> None:
        result = _score({"title": "x"}, search_topics=[f"term{i}" for i in range(60)])
        coverage = _dims(result)["keywordCoverage"]
        assert coverage["total"] == 50
        assert coverage["keywords"] == [f"term{i}" for i in range(50)]
        assert coverage["truncated"] is True

    def test_exactly_fifty_terms_is_not_truncated(self) -> None:
        result = _score({"title": "x"}, search_topics=[f"term{i}" for i in range(50)])
        assert _dims(result)["keywordCoverage"]["truncated"] is False

    def test_generated_keywords_content_key_is_never_a_source(self) -> None:
        """The stage 4 `keywords` key is provider output. It must not make
        the dimension applicable, and it cannot make itself 'covered'."""
        result = _score({"title": "x", "keywords": "canvas case, camera protection"})
        assert _dims(result)["keywordCoverage"] == _NOT_APPLICABLE


# -- Total, rounding, determinism, totality -------------------------------------------


class TestTotal:
    @pytest.mark.parametrize(
        ("earned", "applicable_max", "expected"),
        [
            (62, 75, 83),
            (1, 75, 1),
            (74, 75, 99),
            (56, 75, 75),
            (56, 100, 56),
            (0, 75, 0),
            (75, 75, 100),
        ],
    )
    def test_round_half_up_integer_formula(
        self, earned: int, applicable_max: int, expected: int
    ) -> None:
        assert optimization_quality._round_half_up(earned * 100, applicable_max) == expected

    def test_score_version_is_one(self) -> None:
        assert QUALITY_SCORE_VERSION == 1
        assert _score({"title": "x"}).score_version == 1
        assert _score({"title": "x"}).as_content()["qualityScoreVersion"] == 1

    def test_same_inputs_give_equal_results(self) -> None:
        content = {"title": "Phone Case", "description": "<p>A case.</p>"}
        product = _product(search_topics=["case"])
        first = score_version(content, product)
        second = score_version(content, product)
        assert first == second
        assert first.as_content() == second.as_content()

    @pytest.mark.parametrize(
        "content",
        [{}, {"title": 5, "description": ["x"]}, {"title": {"nested": True}}, None],
    )
    def test_is_total_over_malformed_content(self, content: Any) -> None:
        result = score_version(content, _product())
        assert 0 <= result.score <= 100
        assert result.as_content()["qualityBreakdown"]["dimensions"]["title"]["length"] == 0

    def test_empty_content_scores_only_the_blank_repetition_points(self) -> None:
        # D1 0 + D2 0 + D3 (8 + 8 + 5 + 0) = 21 over 75 → (2100 + 37) ÷ 75 = 28.
        assert _score({}).score == 28


# -- The stub fixture and seoFormat -------------------------------------------------


_STUB_TITLE = "[STUB-AI] synthetic completion (prompt 00000000)."
_STUB_DESCRIPTION = "[STUB-AI] synthetic completion (prompt 11111111)."


class TestStubFixture:
    """Pins what `StubProvider` output happens to score. Not evidence of
    anything about AI quality — the plan (§7.5, §20) says so."""

    def test_stub_content_scores_seventy_five_without_keywords(self) -> None:
        result = _score({"title": _STUB_TITLE, "description": _STUB_DESCRIPTION})
        assert result.score == 75
        assert result.breakdown.earned == 56
        assert result.breakdown.applicable_max == 75

    def test_stub_content_scores_fifty_six_with_keywords(self) -> None:
        result = _score(
            {"title": _STUB_TITLE, "description": _STUB_DESCRIPTION},
            search_topics=["anything"],
        )
        assert result.score == 56
        assert result.breakdown.applicable_max == 100

    def test_seo_format_is_all_true_for_stub_seo_keys(self) -> None:
        result = _score(
            {
                "title": _STUB_TITLE,
                "description": _STUB_DESCRIPTION,
                "seoTitle": _STUB_TITLE,
                "seoDescription": _STUB_TITLE,
                "keywords": _STUB_TITLE,
            }
        )
        assert result.as_content()["qualityBreakdown"]["seoFormat"] == {
            "seoTitleWithinRequestedBound": True,
            "seoDescriptionWithinRequestedBound": True,
            "keywordsPresent": True,
        }


class TestSeoFormat:
    def test_null_when_no_seo_key_is_present(self) -> None:
        assert _score({"title": "x", "description": "y"}).breakdown.seo_format is None
        assert _score({"title": "x"}).as_content()["qualityBreakdown"]["seoFormat"] is None

    def test_sixty_character_seo_title_is_outside_the_requested_bound(self) -> None:
        result = _score({"seoTitle": "x" * 60})
        assert result.breakdown.seo_format is not None
        assert result.breakdown.seo_format.seo_title_within_requested_bound is False

    def test_fifty_nine_character_seo_title_is_within_the_requested_bound(self) -> None:
        result = _score({"seoTitle": "x" * 59})
        assert result.breakdown.seo_format is not None
        assert result.breakdown.seo_format.seo_title_within_requested_bound is True

    def test_seo_title_bound_uses_raw_length_not_plain_text(self) -> None:
        """§7.4 measures ``len(seoTitle)``, not ``len(plain(seoTitle))``.

        Fifty-nine visible characters wrapped in tags would pass if the
        scorer stripped markup first; the stored string is 66 characters
        and is therefore outside the template's requested bound.
        """
        wrapped = f"<b>{'x' * 59}</b>"
        assert len(wrapped) == 66
        result = _score({"seoTitle": wrapped})
        assert result.breakdown.seo_format is not None
        assert result.breakdown.seo_format.seo_title_within_requested_bound is False

    def test_trailing_whitespace_counts_toward_the_seo_title_bound(self) -> None:
        result = _score({"seoTitle": ("x" * 59) + " "})
        assert result.breakdown.seo_format is not None
        assert result.breakdown.seo_format.seo_title_within_requested_bound is False

    def test_seo_format_never_enters_earned(self) -> None:
        without = _score({"title": "Phone Case", "description": "A case."})
        with_seo = _score(
            {"title": "Phone Case", "description": "A case.", "seoTitle": "t", "keywords": "k"}
        )
        assert with_seo.score == without.score
        assert with_seo.breakdown.earned == without.breakdown.earned


# -- Delta contract -----------------------------------------------------------------


_ORIGINAL = {"title": "Phone Case", "description": "<p>Phone Case</p>"}
_IDENTICAL = {"title": "Phone Case", "description": "<p>Phone Case</p>"}
_BETTER = {
    "title": "Durable Canvas Phone Case with Camera Protection",
    "description": (
        "A durable canvas phone case with raised edges for camera protection, a soft "
        "microfibre lining, and precise cut-outs for every port and button. Slim enough "
        "for a pocket and grippy enough for daily use."
    ),
}
_WORSE = {"title": "Case Case Case", "description": "Case Case Case"}


class TestDeltaContract:
    """Four fixtures built from the rules. The numbers are the plan's
    (§16.1 'Delta contract'), asserted as baseline, candidate, and
    subtraction separately, then repeated."""

    @pytest.mark.parametrize(
        ("keywords", "baseline", "identical", "better", "worse", "better_delta", "worse_delta"),
        [
            (None, 52, 52, 100, 41, 48, -11),
            (["camera protection"], 39, 39, 100, 31, 61, -8),
        ],
        ids=["without-keywords", "with-camera-protection"],
    )
    def test_scores_and_deltas(
        self,
        keywords: list[str] | None,
        baseline: int,
        identical: int,
        better: int,
        worse: int,
        better_delta: int,
        worse_delta: int,
    ) -> None:
        product = _product(search_topics=keywords)

        original = score_version(_ORIGINAL, product)
        assert original.score == baseline

        same = score_version(_IDENTICAL, product)
        assert same.score == identical
        assert same.as_content(baseline=original)["qualityDelta"] == 0

        improved = score_version(_BETTER, product)
        assert improved.score == better
        assert improved.as_content(baseline=original)["qualityDelta"] == better - baseline

        degraded = score_version(_WORSE, product)
        assert degraded.score == worse
        assert degraded.as_content(baseline=original)["qualityDelta"] == worse - baseline

        # The plan's literal deltas, stated once more so a drift in either
        # fixture shows up as a number, not an expression.
        assert improved.as_content(baseline=original)["qualityDelta"] == better_delta
        assert degraded.as_content(baseline=original)["qualityDelta"] == worse_delta

        # Second run: identical results, field for field.
        assert score_version(_ORIGINAL, product) == original
        assert score_version(_BETTER, product) == improved
        assert score_version(_WORSE, product) == degraded

    def test_baseline_block_names_the_original_version(self) -> None:
        product = _product()
        original = score_version(_ORIGINAL, product)
        content = score_version(_BETTER, product).as_content(baseline=original)
        assert content["qualityBaseline"] == {
            "versionNumber": ORIGINAL_VERSION_NUMBER,
            "score": 52,
        }
        assert ORIGINAL_VERSION_NUMBER == 1

    def test_content_without_a_baseline_has_no_delta_keys(self) -> None:
        content = _score(_ORIGINAL).as_content()
        assert set(content) == {"qualityScoreVersion", "qualityScore", "qualityBreakdown"}


# -- Module hygiene -------------------------------------------------------------------


class TestModuleHygiene:
    """A score must be computable with no provider, network, or clock."""

    def test_module_imports_nothing_that_could_reach_a_model_or_the_network(self) -> None:
        source = inspect.getsource(optimization_quality)
        imported: set[str] = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        forbidden = ("app.ai", "app.services.prompt", "app.services.seo_score", "httpx")
        for name in imported:
            for prefix in forbidden:
                assert not (name == prefix or name.startswith(prefix + ".")), name
        assert not imported & {"asyncio", "random", "datetime"}

    def test_private_helper_is_not_exported(self) -> None:
        assert "_merchant_terms" not in optimization_quality.__all__
        assert "score_version" in optimization_quality.__all__

    def test_scoring_needs_no_provider_configuration(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """If the scorer ever reached for the provider factory, this would
        raise instead of returning a number."""
        import app.ai.factory as factory

        def _boom(_settings: object) -> object:
            raise AssertionError("score_version must not resolve an AI provider")

        monkeypatch.setattr(factory, "get_ai_provider", _boom)
        # D1 12 (len 10) + D2 0 + D3 21 = 33 over 75 → (3300 + 37) ÷ 75 = 44.
        assert _score({"title": "Phone Case"}).score == 44
