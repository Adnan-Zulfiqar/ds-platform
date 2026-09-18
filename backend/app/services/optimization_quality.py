"""Deterministic, model-free optimisation-quality score for product versions.

Phase 9 stage 5. Contract: ``docs/PHASE_9_STAGE_5_PLAN.md`` — every rule
here is a line in that document, and every number is asserted exactly by
``tests/unit/test_optimization_quality.py``.

What this measures, and what it does not. A ``ProductVersion.content``
snapshot is scored on four things a reader could check by eye: title
length, description length, repetition, and whether the merchant's own
keywords appear. That is the whole rubric. It is not marketplace ranking,
conversion, or model quality, and it never calls a provider — the same
inputs produce the same integer every time, which is the property a delta
between two versions depends on.

Kept apart from two neighbours on purpose. ``app.services.seo_score`` is
the merchant-listing SEO advisory score over the *current* ``Product``;
this module scores an immutable *version* and imports nothing from it. The
seeded ``quality_scorer`` prompt stays unwired; nothing here touches
``app.ai``.

Only ``score_version`` and the result types are public. ``_merchant_terms``
is deliberately private: it parses keyword terms for scoring and nothing
else, so that stage 4's ``_keywords_for_prompt`` — whose exact output is
hashed into every recorded prompt execution — is never tempted into
sharing it.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import pairwise
from typing import Any, Literal

from app.core.sanitize import html_to_plain_text
from app.models.product import Product

#: Bumped whenever a rule in this module changes. Persisted with every score
#: so two versions carrying different rubric versions are never compared as
#: if they were scored the same way. Existing rows are never recomputed.
QUALITY_SCORE_VERSION = 1

#: The original supplier snapshot; every AI-generated version is compared
#: to it, never to the previous AI version (plan §5.4).
ORIGINAL_VERSION_NUMBER = 1

_DIMENSION_MAX = 25
_MAX_TERMS = 50
_MIN_TOKENS_FOR_DOMINANCE = 20
_TOKEN_RE = re.compile(r"[A-Za-z0-9']+")
_WHITESPACE_RE = re.compile(r"\s+")

# Bounds the seeded `seo_optimizer` template itself asks for ("under 60
# characters", "under 155 characters"). `seoFormat` checks the response
# against the request; it does not judge the response.
_SEO_TITLE_BOUND = 60
_SEO_DESCRIPTION_BOUND = 155

MerchantTermSource = Literal["search_topics", "tags", "meta_keywords"]


# -- Result types ---------------------------------------------------------------
#
# Frozen dataclasses so two results for the same inputs compare equal with
# `==`, which is how the tests state "deterministic". `as_content()` on the
# top-level result is the only place the persisted JSON shape is spelled
# out; the schema layer mirrors it in `ProductVersionQualityBreakdownRead`.


@dataclass(frozen=True)
class LengthDimension:
    points: int
    max: int
    applicable: bool
    length: int


@dataclass(frozen=True)
class RepetitionChecks:
    title_not_stuffed: bool
    description_not_phrase_stuffed: bool
    description_not_dominated: bool
    description_distinct_from_title: bool


@dataclass(frozen=True)
class RepetitionDimension:
    points: int
    max: int
    applicable: bool
    checks: RepetitionChecks


@dataclass(frozen=True)
class KeywordCoverage:
    applicable: bool
    points: int
    max: int
    matched: int
    total: int
    source: MerchantTermSource | None
    keywords: tuple[str, ...]
    truncated: bool


@dataclass(frozen=True)
class SeoFormat:
    seo_title_within_requested_bound: bool
    seo_description_within_requested_bound: bool
    keywords_present: bool


@dataclass(frozen=True)
class QualityBreakdown:
    earned: int
    applicable_max: int
    title: LengthDimension
    description: LengthDimension
    repetition: RepetitionDimension
    keyword_coverage: KeywordCoverage
    seo_format: SeoFormat | None


@dataclass(frozen=True)
class QualityResult:
    score_version: int
    score: int
    breakdown: QualityBreakdown

    def as_content(self, *, baseline: QualityResult | None = None) -> dict[str, Any]:
        """The stage 5 keys to merge into ``ProductVersion.content``.

        With ``baseline`` — the original version's result, recomputed at the
        same moment with the same merchant terms — the AI-version keys
        ``qualityBaseline`` and ``qualityDelta`` are included. Without it,
        only the version's own score is recorded, which is the original's
        shape. This is the one code path that computes a delta, so the
        service and the tests cannot disagree about the subtraction.
        """
        content: dict[str, Any] = {
            "qualityScoreVersion": self.score_version,
            "qualityScore": self.score,
            "qualityBreakdown": _breakdown_as_content(self.breakdown),
        }
        if baseline is not None:
            content["qualityBaseline"] = {
                "versionNumber": ORIGINAL_VERSION_NUMBER,
                "score": baseline.score,
            }
            content["qualityDelta"] = self.score - baseline.score
        return content


@dataclass(frozen=True)
class _MerchantTerms:
    source: MerchantTermSource | None
    terms: tuple[str, ...]
    truncated: bool


# -- Public API -----------------------------------------------------------------


def score_version(content: Mapping[str, Any] | None, product: Product) -> QualityResult:
    """Score one version snapshot against the merchant's keywords.

    Total over its inputs: any non-string value is treated as blank and a
    non-mapping ``content`` as empty, so a malformed row produces a low
    score rather than an exception. Reads ``search_topics`` / ``tags`` /
    ``meta_keywords`` from ``product`` and writes nothing anywhere.
    """
    data: Mapping[str, Any] = content if isinstance(content, Mapping) else {}
    title = data.get("title")
    description = data.get("description")

    title_dimension = _score_title(title)
    description_dimension = _score_description(description)
    repetition = _score_repetition(title, description)
    coverage = _score_keyword_coverage(title, description, _merchant_terms(product))

    earned = title_dimension.points + description_dimension.points + repetition.points
    applicable_max = 3 * _DIMENSION_MAX
    if coverage.applicable:
        earned += coverage.points
        applicable_max += _DIMENSION_MAX

    breakdown = QualityBreakdown(
        earned=earned,
        applicable_max=applicable_max,
        title=title_dimension,
        description=description_dimension,
        repetition=repetition,
        keyword_coverage=coverage,
        seo_format=_seo_format(data),
    )
    return QualityResult(
        score_version=QUALITY_SCORE_VERSION,
        score=_round_half_up(earned * 100, applicable_max),
        breakdown=breakdown,
    )


# -- Normalisation ----------------------------------------------------------------


def _plain(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return _WHITESPACE_RE.sub(" ", html_to_plain_text(value)).strip()


def _tokens(value: object) -> list[str]:
    return [token.lower() for token in _TOKEN_RE.findall(_plain(value))]


def _round_half_up(numerator: int, denominator: int) -> int:
    """Integer round-half-up of ``numerator / denominator``.

    Not ``round()``: Python rounds half to even, and the contract is half
    up. The plan checked this form exhaustively for both denominators the
    total uses and for every keyword ratio up to fifty terms.
    """
    return (numerator + denominator // 2) // denominator


# -- Dimensions -------------------------------------------------------------------


def _score_title(title: object) -> LengthDimension:
    length = len(_plain(title))
    if length == 0:
        points = 0
    elif length <= 9:
        points = 6
    elif length <= 19:
        points = 12
    elif length <= 120:
        points = 25
    elif length <= 255:
        points = 15
    else:
        # Beyond Shopify's documented 255-character product-title limit —
        # storable here, but not publishable unmodified.
        points = 0
    return LengthDimension(points=points, max=_DIMENSION_MAX, applicable=True, length=length)


def _score_description(description: object) -> LengthDimension:
    length = len(_plain(description))
    if length == 0:
        points = 0
    elif length <= 49:
        points = 6
    elif length <= 149:
        points = 12
    elif length <= 2000:
        points = 25
    elif length <= 5000:
        points = 18
    else:
        points = 12
    return LengthDimension(points=points, max=_DIMENSION_MAX, applicable=True, length=length)


def _score_repetition(title: object, description: object) -> RepetitionDimension:
    title_tokens = _tokens(title)
    description_tokens = _tokens(description)

    # D3a — no token of three or more characters three or more times.
    title_counts = Counter(token for token in title_tokens if len(token) >= 3)
    title_not_stuffed = all(count < 3 for count in title_counts.values())

    # D3b — no adjacent-token bigram three or more times.
    bigram_counts = Counter(pairwise(description_tokens))
    description_not_phrase_stuffed = all(count < 3 for count in bigram_counts.values())

    # D3c — with at least twenty tokens, no single token of three or more
    # characters makes up more than a quarter of them. Fewer than twenty is
    # not evaluable and awards: there is no evidence of stuffing. The
    # comparison is kept in integers (count * 4 > total) so "exactly 25 %"
    # passes without a floating-point edge.
    if len(description_tokens) < _MIN_TOKENS_FOR_DOMINANCE:
        description_not_dominated = True
    else:
        long_counts = Counter(token for token in description_tokens if len(token) >= 3)
        dominant = max(long_counts.values(), default=0)
        description_not_dominated = dominant * 4 <= len(description_tokens)

    # D3d — a non-blank description that is not the title again.
    plain_description = _plain(description)
    description_distinct_from_title = (
        plain_description != "" and plain_description.lower() != _plain(title).lower()
    )

    checks = RepetitionChecks(
        title_not_stuffed=title_not_stuffed,
        description_not_phrase_stuffed=description_not_phrase_stuffed,
        description_not_dominated=description_not_dominated,
        description_distinct_from_title=description_distinct_from_title,
    )
    points = (
        (8 if title_not_stuffed else 0)
        + (8 if description_not_phrase_stuffed else 0)
        + (5 if description_not_dominated else 0)
        + (4 if description_distinct_from_title else 0)
    )
    return RepetitionDimension(points=points, max=_DIMENSION_MAX, applicable=True, checks=checks)


def _score_keyword_coverage(
    title: object, description: object, terms: _MerchantTerms
) -> KeywordCoverage:
    if not terms.terms:
        # The merchant provided no keywords: the dimension is not applicable
        # and contributes neither earned nor available points. `max` stays
        # the nominal 25 so the shape never changes; `applicableMax` on the
        # breakdown is the only denominator. Nothing is invented to fill the
        # gap — not from the title, and never from generated output.
        return KeywordCoverage(
            applicable=False,
            points=0,
            max=_DIMENSION_MAX,
            matched=0,
            total=0,
            source=None,
            keywords=(),
            truncated=False,
        )

    haystack = f"{_plain(title).lower()} {_plain(description).lower()}"
    # Substring, not word, match — deliberately lenient: "case" matches
    # "cases". Word-boundary or stemmed matching would be a language
    # judgment the rubric refuses to make.
    matched = sum(1 for term in terms.terms if term.lower() in haystack)
    total = len(terms.terms)
    return KeywordCoverage(
        applicable=True,
        points=_round_half_up(_DIMENSION_MAX * matched, total),
        max=_DIMENSION_MAX,
        matched=matched,
        total=total,
        source=terms.source,
        keywords=terms.terms,
        truncated=terms.truncated,
    )


def _stored_text(value: object) -> str:
    """The stored Stage 4 field as the template asked to produce it.

    ``seoFormat`` is a format check on the raw completion (§7.4:
    ``len(seoTitle) < 60``), not a D1–D4 measurement. Stripping tags or
    collapsing whitespace here would report the stub's markup-wrapped
    59-character title as in-bound when the stored string is not.
    """
    return value if isinstance(value, str) else ""


def _seo_format(content: Mapping[str, Any]) -> SeoFormat | None:
    """Format checks on the stage 4 SEO keys, recorded but never scored.

    ``None`` when the version carries no SEO key at all — the original
    snapshot, or a row written before stage 4.
    """
    if not any(key in content for key in ("seoTitle", "seoDescription", "keywords")):
        return None
    seo_title = _stored_text(content.get("seoTitle"))
    seo_description = _stored_text(content.get("seoDescription"))
    keywords = _stored_text(content.get("keywords"))
    return SeoFormat(
        seo_title_within_requested_bound=seo_title != "" and len(seo_title) < _SEO_TITLE_BOUND,
        seo_description_within_requested_bound=(
            seo_description != "" and len(seo_description) < _SEO_DESCRIPTION_BOUND
        ),
        keywords_present=keywords != "",
    )


# -- Merchant terms (private) ---------------------------------------------------


def _merchant_terms(product: Product) -> _MerchantTerms:
    """The merchant's keyword terms, for scoring only.

    Same source order as stage 4's ``_keywords_for_prompt`` — search topics,
    then tags, then the legacy ``meta_keywords`` text — so both stages agree
    on *whose* keywords count. What happens to them afterwards differs and
    must stay separate: stage 4 joins the raw entries into one prompt
    variable and leaves ``meta_keywords`` unsplit, because that string is
    hashed into every recorded execution; this function splits, strips,
    de-duplicates, and caps because scoring needs individual terms. Neither
    calls the other.
    """
    candidates: list[tuple[MerchantTermSource, Sequence[object]]] = [
        ("search_topics", list(product.search_topics or [])),
        ("tags", list(product.tags or [])),
        ("meta_keywords", (product.meta_keywords or "").split(",")),
    ]
    for source, raw in candidates:
        terms: list[str] = []
        seen: set[str] = set()
        for entry in raw:
            if not isinstance(entry, str):
                continue
            term = entry.strip()
            if not term or term.lower() in seen:
                continue
            seen.add(term.lower())
            terms.append(term)
        if terms:
            return _MerchantTerms(
                source=source,
                terms=tuple(terms[:_MAX_TERMS]),
                truncated=len(terms) > _MAX_TERMS,
            )
    return _MerchantTerms(source=None, terms=(), truncated=False)


# -- Persisted shape ----------------------------------------------------------------


def _breakdown_as_content(breakdown: QualityBreakdown) -> dict[str, Any]:
    coverage = breakdown.keyword_coverage
    seo_format = breakdown.seo_format
    return {
        "earned": breakdown.earned,
        "applicableMax": breakdown.applicable_max,
        "dimensions": {
            "title": _length_as_content(breakdown.title),
            "description": _length_as_content(breakdown.description),
            "repetition": {
                "points": breakdown.repetition.points,
                "max": breakdown.repetition.max,
                "applicable": breakdown.repetition.applicable,
                "checks": {
                    "titleNotStuffed": breakdown.repetition.checks.title_not_stuffed,
                    "descriptionNotPhraseStuffed": (
                        breakdown.repetition.checks.description_not_phrase_stuffed
                    ),
                    "descriptionNotDominated": (
                        breakdown.repetition.checks.description_not_dominated
                    ),
                    "descriptionDistinctFromTitle": (
                        breakdown.repetition.checks.description_distinct_from_title
                    ),
                },
            },
            "keywordCoverage": {
                "applicable": coverage.applicable,
                "points": coverage.points,
                "max": coverage.max,
                "matched": coverage.matched,
                "total": coverage.total,
                "source": coverage.source,
                "keywords": list(coverage.keywords),
                "truncated": coverage.truncated,
            },
        },
        "seoFormat": (
            None
            if seo_format is None
            else {
                "seoTitleWithinRequestedBound": seo_format.seo_title_within_requested_bound,
                "seoDescriptionWithinRequestedBound": (
                    seo_format.seo_description_within_requested_bound
                ),
                "keywordsPresent": seo_format.keywords_present,
            }
        ),
    }


def _length_as_content(dimension: LengthDimension) -> dict[str, Any]:
    return {
        "points": dimension.points,
        "max": dimension.max,
        "applicable": dimension.applicable,
        "length": dimension.length,
    }


__all__ = [
    "ORIGINAL_VERSION_NUMBER",
    "QUALITY_SCORE_VERSION",
    "KeywordCoverage",
    "LengthDimension",
    "QualityBreakdown",
    "QualityResult",
    "RepetitionChecks",
    "RepetitionDimension",
    "SeoFormat",
    "score_version",
]
