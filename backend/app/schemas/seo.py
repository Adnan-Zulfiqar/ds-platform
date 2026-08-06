"""SEO workspace response schemas."""

from __future__ import annotations

from typing import Any

from app.schemas.base import CamelCaseModel


class SeoScoreRead(CamelCaseModel):
    score: int
    status: str
    sections: dict[str, int]
    warnings: list[str]
    explanations: list[str]
    meta_keywords_exported: bool = False
    note: str = (
        "Advisory score only — not a ranking guarantee. "
        "Search topics are planning inputs and are never exported as meta keywords."
    )
    details: dict[str, Any] | None = None
