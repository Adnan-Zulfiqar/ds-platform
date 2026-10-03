"""eBay Taxonomy API: category suggestions and required item aspects (EBAY-C3).

Served to the *application*, not the seller, so every call carries the
client-credentials token from ``tokens.application_access_token`` — no new
seller scope and no seller consent (D-C3-3).

Category trees change rarely; the tree id per marketplace is cached in
process memory. Suggestions and aspects are fetched when the merchant opens
the eBay details of a product, never in bulk.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import settings
from app.core.logging import get_logger
from app.integrations.ebay.exceptions import (
    EbayNotConfiguredError,
    EbayRateLimitedError,
    EbaySellerApiUnavailableError,
    EbayTokenExchangeError,
    EbayTokenRevokedError,
)
from app.integrations.ebay.tokens import application_access_token, forget_application_token

logger = get_logger(__name__)

_tree_ids: dict[tuple[str, str], str] = {}

#: Aspect values eBay offers per aspect can number in the thousands; the
#: details form shows enough to pick from and accepts free text otherwise.
_MAX_ASPECT_VALUES = 50
_MAX_SUGGESTIONS = 10


@dataclass(frozen=True, slots=True)
class EbayCategorySuggestion:
    category_id: str
    name: str
    path: str


@dataclass(frozen=True, slots=True)
class EbayAspect:
    name: str
    required: bool
    #: ``SELECTION_ONLY`` aspects accept only listed values; ``FREE_TEXT`` any.
    selection_only: bool
    multiple: bool
    values: tuple[str, ...]


def _text(mapping: Mapping[str, Any], key: str) -> str | None:
    value = mapping.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None


def parse_suggestions(payload: Any) -> tuple[EbayCategorySuggestion, ...]:
    if not isinstance(payload, Mapping):
        raise EbaySellerApiUnavailableError("eBay returned malformed category suggestions.")
    items = payload.get("categorySuggestions") or []
    out: list[EbayCategorySuggestion] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, Mapping):
            continue
        category = item.get("category")
        if not isinstance(category, Mapping):
            continue
        category_id = _text(category, "categoryId")
        name = _text(category, "categoryName")
        if category_id is None or name is None:
            continue
        ancestors = item.get("categoryTreeNodeAncestors") or []
        # eBay lists ancestors nearest-first; a breadcrumb reads root-first.
        names = [
            n
            for a in reversed(ancestors if isinstance(ancestors, list) else [])
            if isinstance(a, Mapping) and (n := _text(a, "categoryName"))
        ]
        out.append(
            EbayCategorySuggestion(
                category_id=category_id, name=name, path=" > ".join([*names, name])
            )
        )
    return tuple(out[:_MAX_SUGGESTIONS])


def parse_aspects(payload: Any) -> tuple[EbayAspect, ...]:
    if not isinstance(payload, Mapping):
        raise EbaySellerApiUnavailableError("eBay returned malformed item aspects.")
    items = payload.get("aspects") or []
    out: list[EbayAspect] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, Mapping):
            continue
        name = _text(item, "localizedAspectName")
        if name is None:
            continue
        constraint = item.get("aspectConstraint")
        constraint = constraint if isinstance(constraint, Mapping) else {}
        raw_values = item.get("aspectValues") or []
        values = tuple(
            v
            for entry in (raw_values if isinstance(raw_values, list) else [])
            if isinstance(entry, Mapping) and (v := _text(entry, "localizedValue"))
        )[:_MAX_ASPECT_VALUES]
        out.append(
            EbayAspect(
                name=name,
                required=constraint.get("aspectRequired") is True,
                selection_only=constraint.get("aspectMode") == "SELECTION_ONLY",
                multiple=constraint.get("itemToAspectCardinality") == "MULTI",
                values=values,
            )
        )
    # Required first, then eBay's order: the form leads with what blocks a publish.
    return tuple(sorted(out, key=lambda a: not a.required))


async def _get(path: str, *, params: Mapping[str, str], call: str) -> Any:
    timeout = httpx.Timeout(
        settings.ebay.request_timeout_seconds,
        connect=settings.ebay.connect_timeout_seconds,
    )
    try:
        token = await application_access_token()
    except (
        httpx.HTTPError,
        EbayTokenExchangeError,
        EbayTokenRevokedError,
        EbayNotConfiguredError,
    ) as exc:
        # The *application's* credential failed, not a seller grant: telling
        # the merchant to reconnect eBay could not help. Report it as eBay
        # being unavailable, which readiness turns into "try again".
        logger.warning("ebay_application_token_unavailable", call=call, error=type(exc).__name__)
        raise EbaySellerApiUnavailableError() from exc
    url = f"{settings.ebay.notification_api_base}{path}"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(
                url,
                params=params,
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            )
    except httpx.HTTPError as exc:
        logger.warning("ebay_taxonomy_unreachable", call=call, error=type(exc).__name__)
        raise EbaySellerApiUnavailableError() from exc
    if response.status_code in (httpx.codes.UNAUTHORIZED, httpx.codes.FORBIDDEN):
        # An application token, not a seller grant: nothing to reconnect.
        # Drop it so the next call mints a fresh one.
        forget_application_token()
        logger.warning("ebay_taxonomy_unauthorized", call=call, status_code=response.status_code)
        raise EbaySellerApiUnavailableError()
    if response.status_code == httpx.codes.TOO_MANY_REQUESTS:
        logger.warning("ebay_taxonomy_rate_limited", call=call)
        raise EbayRateLimitedError()
    if response.status_code != httpx.codes.OK:
        logger.warning("ebay_taxonomy_failed", call=call, status_code=response.status_code)
        raise EbaySellerApiUnavailableError()
    try:
        return response.json()
    except ValueError as exc:
        raise EbaySellerApiUnavailableError("eBay returned a non-JSON response.") from exc


async def category_tree_id(marketplace_id: str) -> str:
    key = (settings.ebay.environment.value, marketplace_id)
    cached = _tree_ids.get(key)
    if cached is not None:
        return cached
    body = await _get(
        "/commerce/taxonomy/v1/get_default_category_tree_id",
        params={"marketplace_id": marketplace_id},
        call="category_tree_id",
    )
    tree_id = _text(body, "categoryTreeId") if isinstance(body, Mapping) else None
    if tree_id is None:
        raise EbaySellerApiUnavailableError("eBay returned no category tree for that marketplace.")
    _tree_ids[key] = tree_id
    return tree_id


async def category_suggestions(
    marketplace_id: str, query: str
) -> tuple[EbayCategorySuggestion, ...]:
    tree_id = await category_tree_id(marketplace_id)
    body = await _get(
        f"/commerce/taxonomy/v1/category_tree/{tree_id}/get_category_suggestions",
        params={"q": query[:350]},
        call="category_suggestions",
    )
    return parse_suggestions(body)


async def item_aspects(marketplace_id: str, category_id: str) -> tuple[EbayAspect, ...]:
    tree_id = await category_tree_id(marketplace_id)
    body = await _get(
        f"/commerce/taxonomy/v1/category_tree/{tree_id}/get_item_aspects_for_category",
        params={"category_id": category_id},
        call="item_aspects",
    )
    return parse_aspects(body)


def forget_category_trees() -> None:
    """Tests only: clear the per-process tree id cache."""
    _tree_ids.clear()


__all__ = [
    "EbayAspect",
    "EbayCategorySuggestion",
    "category_suggestions",
    "category_tree_id",
    "forget_category_trees",
    "item_aspects",
    "parse_aspects",
    "parse_suggestions",
]
