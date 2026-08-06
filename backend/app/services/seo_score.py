"""Transparent ecommerce SEO quality score (advisory, no ranking guarantees)."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

from app.models.product import Product

_WORD_RE = re.compile(r"[A-Za-z0-9']+")
_ALL_CAPS_RE = re.compile(r"\b[A-Z]{5,}\b")
_SUPERLATIVES = frozenset(
    {"best", "cheapest", "#1", "number one", "guaranteed", "miracle", "instant"}
)


@dataclass(frozen=True)
class SeoScoreBreakdown:
    score: int
    status: str
    sections: dict[str, int]
    warnings: list[str]
    explanations: list[str]


def score_product_seo(product: Product) -> SeoScoreBreakdown:
    """Compute 0-100 SEO score with explicit section points and warnings."""
    sections: dict[str, int] = {
        "search_intent": 0,
        "title_quality": 0,
        "description_quality": 0,
        "uniqueness": 0,
        "content_completeness": 0,
        "images_alt": 0,
        "url_handle": 0,
        "structured_data": 0,
        "indexability": 0,
        "compliance": 10,  # start full; deduct for spam signals
    }
    warnings: list[str] = []
    explanations: list[str] = []

    planning = product.seo_planning or {}
    if planning.get("primary_topic") or planning.get("primaryTopic"):
        sections["search_intent"] += 8
        explanations.append("+8 search intent: primary topic set")
    if planning.get("primary_search_intent") or planning.get("primarySearchIntent"):
        sections["search_intent"] += 7
        explanations.append("+7 search intent: intent selected")
    else:
        warnings.append("Primary search intent not set (planning only)")

    seo_title = (product.seo_title or "").strip()
    title = (product.title or "").strip()
    use_title = seo_title or title
    if use_title:
        length = len(use_title)
        if 30 <= length <= 65:
            sections["title_quality"] += 12
            explanations.append("+12 title: length in recommended range")
        elif 15 <= length < 30 or 65 < length <= 80:
            sections["title_quality"] += 7
            explanations.append("+7 title: length acceptable but not ideal")
            warnings.append("SEO title length outside the usual 30-65 character range")
        else:
            sections["title_quality"] += 2
            warnings.append("SEO title is too short or very long for SERP previews")
        if _has_repeated_phrase(use_title):
            sections["title_quality"] = max(0, sections["title_quality"] - 6)
            warnings.append("Repeated phrase detected in SEO title")
        if _keyword_stuffing(use_title):
            sections["compliance"] = max(0, sections["compliance"] - 5)
            warnings.append("Possible keyword stuffing in SEO title")
        if _ALL_CAPS_RE.search(use_title):
            sections["compliance"] = max(0, sections["compliance"] - 3)
            warnings.append("All-caps spam pattern in SEO title")
        if seo_title and seo_title.lower() == title.lower():
            warnings.append("SEO title matches product title — consider a customer-focused rewrite")
        else:
            sections["title_quality"] += 3
            explanations.append("+3 title: SEO title differs from product title")

    seo_desc = (product.seo_description or "").strip()
    if seo_desc:
        dlen = len(seo_desc)
        if 70 <= dlen <= 165:
            sections["description_quality"] += 12
            explanations.append("+12 meta description: length in recommended range")
        elif dlen > 0:
            sections["description_quality"] += 5
            warnings.append("Meta description length outside the usual 70-165 character range")
        if seo_title and seo_desc.lower() == seo_title.lower():
            sections["description_quality"] = max(0, sections["description_quality"] - 4)
            warnings.append("Meta description copies the SEO title")
        if _keyword_stuffing(seo_desc):
            sections["compliance"] = max(0, sections["compliance"] - 4)
            warnings.append("Possible keyword stuffing in meta description")
        if any(token in seo_desc.lower() for token in _SUPERLATIVES):
            sections["compliance"] = max(0, sections["compliance"] - 2)
            warnings.append("Unsupported superlative language in meta description")
    else:
        warnings.append("Meta description missing")

    description = (product.description or "").strip()
    if len(description) >= 200:
        sections["content_completeness"] += 10
        explanations.append("+10 content: description has substantial length")
    elif description:
        sections["content_completeness"] += 4
        warnings.append("Thin product description")
    else:
        warnings.append("Product description missing")

    supplier = (product.supplier_description or "").strip()
    if description and supplier and _similarity_high(description, supplier):
        sections["uniqueness"] = 0
        warnings.append("Description is very similar to supplier copy")
    elif description:
        sections["uniqueness"] += 8
        explanations.append("+8 uniqueness: merchant description diverges from supplier")

    images = [img for img in (product.images or []) if getattr(img, "deleted_at", None) is None]
    if images:
        sections["images_alt"] += 5
        with_alt = sum(1 for img in images if (getattr(img, "alt_text", None) or "").strip())
        if with_alt == len(images):
            sections["images_alt"] += 7
            explanations.append("+7 images: all have alt text")
        elif with_alt:
            sections["images_alt"] += 3
            warnings.append(f"Missing alt text on {len(images) - with_alt} image(s)")
        else:
            warnings.append("No image alt text set")
    else:
        warnings.append("No images for SEO/social preview")

    if product.slug:
        sections["url_handle"] += 8
        explanations.append("+8 URL handle present")
        if re.search(r"[^a-z0-9\-]", product.slug):
            sections["url_handle"] = max(0, sections["url_handle"] - 3)
            warnings.append("URL handle contains characters outside a-z, 0-9, hyphen")
    else:
        warnings.append("URL handle (slug) missing")

    # Structured-data readiness (payload expectations — not theme injection).
    sd = 0
    if product.title:
        sd += 2
    if images:
        sd += 2
    if description:
        sd += 2
    enabled = [v for v in (product.variants or []) if getattr(v, "is_enabled", True)]
    if any(getattr(v, "sell_price", None) or v.list_price for v in enabled) or product.sell_price:
        sd += 2
    if product.currency:
        sd += 1
    if any((getattr(v, "merchant_sku", None) or v.external_variant_id) for v in enabled):
        sd += 1
    sections["structured_data"] = min(10, sd)
    explanations.append(
        f"+{sections['structured_data']} structured-data readiness from payload fields"
    )

    if product.status and product.status.value in {"active", "draft"}:
        sections["indexability"] += 5
    if product.requires_shipping is False or product.package_weight_kg is not None:
        sections["indexability"] += 3
    else:
        warnings.append("Shipping/physical data incomplete for Offer readiness")

    topics = product.search_topics or []
    if topics:
        explanations.append(
            "Search topics are planning inputs only - not exported as meta keywords"
        )

    score = max(0, min(100, sum(sections.values())))
    if score >= 85:
        status = "Excellent"
    elif score >= 70:
        status = "Good"
    elif score >= 45:
        status = "Needs Work"
    else:
        status = "Poor"

    return SeoScoreBreakdown(
        score=score,
        status=status,
        sections=sections,
        warnings=warnings,
        explanations=explanations,
    )


def _words(text: str) -> list[str]:
    return [w.lower() for w in _WORD_RE.findall(text)]


def _has_repeated_phrase(text: str) -> bool:
    words = _words(text)
    if len(words) < 4:
        return False
    bigrams = [" ".join(words[i : i + 2]) for i in range(len(words) - 1)]
    counts = Counter(bigrams)
    return any(count >= 2 for phrase, count in counts.items() if " " in phrase)


def _keyword_stuffing(text: str) -> bool:
    words = [w for w in _words(text) if len(w) > 2]
    if len(words) < 6:
        return False
    counts = Counter(words)
    most = counts.most_common(1)[0][1]
    return most / len(words) >= 0.28


def _similarity_high(a: str, b: str) -> bool:
    wa, wb = set(_words(a)), set(_words(b))
    if not wa or not wb:
        return False
    overlap = len(wa & wb) / max(len(wa), len(wb))
    return overlap >= 0.85


def seo_score_dict(product: Product) -> dict[str, Any]:
    result = score_product_seo(product)
    return {
        "score": result.score,
        "status": result.status,
        "sections": result.sections,
        "warnings": result.warnings,
        "explanations": result.explanations,
        "meta_keywords_exported": False,
    }
