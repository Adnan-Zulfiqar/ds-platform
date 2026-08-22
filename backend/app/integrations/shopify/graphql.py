"""Shopify Admin GraphQL client (GQL-1 foundation).

One asynchronous client that every future GraphQL operation goes through. It
does transport, safety and classification — nothing about products, inventory or
orders, which belong to the operation layer added in GQL-2 onwards.

**Why this exists at all.** Shopify requires new public apps submitted to the App
Store to use GraphQL (changelog: "all new public apps submitted to the App Store
after this date must only use GraphQL", effective 1 April 2025). The existing
``ShopifyClient`` is REST-first with a GraphQL method bolted on; it retries
mutations, treats top-level ``errors`` as a generic response failure, ignores
``userErrors`` entirely and never looks at cost. Rather than deepen that, this is
the surface the migration moves onto, and ``ShopifyClient`` stays untouched until
each REST call is migrated in its own phase.

**Verified against official documentation, 2026-07** (see
``docs/shopify-graphql/GQL1_FOUNDATION.md`` for the citations):

* endpoint ``https://{shop}.myshopify.com/admin/api/2026-07/graphql.json``
* ``POST``, ``X-Shopify-Access-Token``, ``Content-Type: application/json``
* ``extensions.cost`` → ``requestedQueryCost``, ``actualQueryCost``,
  ``throttleStatus.{maximumAvailable,currentlyAvailable,restoreRate}``
* ``errors[].extensions.code`` → ``THROTTLED``, ``MAX_COST_EXCEEDED``,
  ``ACCESS_DENIED``, ``SHOP_INACTIVE``, ``INTERNAL_SERVER_ERROR``

Nothing here was written from memory of the schema; anything not confirmed in
the official reference is marked ``unverified`` in the REST inventory instead of
being guessed at.

**Retry eligibility comes from the parsed document**, not from a caller's
declaration -- see ``operations.py``. That was an acceptance finding (F-02): a
mutation declared as a query was eligible for automatic retry, which is three
chances to create three products.
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from email.utils import parsedate_to_datetime
from typing import Any, Final

import httpx

from app.core.config import settings
from app.core.logging import get_logger
from app.integrations.shopify.auth import is_canonical_shop_domain
from app.integrations.shopify.exceptions import (
    ShopifyAuthError,
    ShopifyError,
    ShopifyGraphQLError,
    ShopifyInvalidShopError,
    ShopifyQueryCostError,
    ShopifyResponseError,
    ShopifyThrottledError,
    ShopifyTimeoutError,
    ShopifyUserError,
)
from app.integrations.shopify.operations import (
    OperationType,
    SelectedOperation,
    assert_declaration_matches,
    select_operation,
)

logger = get_logger(__name__)

#: Shopify's own request identifier header. Worth capturing for support tickets;
#: it names a request, never its contents.
REQUEST_ID_HEADER: Final = "X-Request-Id"

#: Error codes from ``errors[].extensions.code``. Only the ones this client acts
#: on differently are named; anything else falls through to a generic failure.
THROTTLED_CODE: Final = "THROTTLED"
MAX_COST_CODE: Final = "MAX_COST_EXCEEDED"
ACCESS_DENIED_CODE: Final = "ACCESS_DENIED"

#: HTTP statuses worth another attempt. 500 is deliberately absent: Shopify
#: returns it for genuinely broken documents as well as for its own faults, and
#: hammering a bad document helps nobody.
_RETRYABLE_STATUSES: Final = frozenset({502, 503, 504})

_MAX_BACKOFF_SECONDS: Final[float] = 30.0
_MAX_TOTAL_WAIT_SECONDS: Final[float] = 60.0


@dataclass(frozen=True, slots=True)
class ThrottleStatus:
    """``extensions.cost.throttleStatus``, all optional-safe."""

    maximum_available: Decimal | None = None
    currently_available: Decimal | None = None
    restore_rate: Decimal | None = None


@dataclass(frozen=True, slots=True)
class QueryCost:
    """``extensions.cost``.

    Every field is optional. Cost metadata is telemetry, and a malformed or
    absent ``extensions`` block must never turn a successful response into a
    failure — the merchant's data arrived either way.
    """

    requested: Decimal | None = None
    actual: Decimal | None = None
    throttle: ThrottleStatus = field(default_factory=ThrottleStatus)


@dataclass(frozen=True, slots=True)
class GraphQLResponse:
    """What a successful call returns.

    Carries no headers and no token — only the handful of facts a caller or an
    operator legitimately needs. Returning the raw response would make it far too
    easy for a log line downstream to print an ``Authorization``-adjacent header.
    """

    data: Mapping[str, Any]
    #: The selected operation's name, or ``None`` for an anonymous operation --
    #: which GraphQL permits when a document holds exactly one.
    operation_name: str | None
    api_version: str
    request_id: str | None = None
    cost: QueryCost = field(default_factory=QueryCost)
    attempts: int = 1


def _to_decimal(value: object) -> Decimal | None:
    """Coerce a cost figure, or give up quietly.

    Type-validated but never fatal: Shopify sending a string, a null or a new
    shape must not crash the parse of a response whose ``data`` is perfectly
    good.
    """
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int | float | str):
        try:
            return Decimal(str(value))
        except (ArithmeticError, ValueError):
            return None
    return None


def parse_cost(extensions: object) -> QueryCost:
    """Read ``extensions.cost`` defensively."""
    if not isinstance(extensions, Mapping):
        return QueryCost()
    cost = extensions.get("cost")
    if not isinstance(cost, Mapping):
        return QueryCost()
    throttle_raw = cost.get("throttleStatus")
    throttle = ThrottleStatus()
    if isinstance(throttle_raw, Mapping):
        throttle = ThrottleStatus(
            maximum_available=_to_decimal(throttle_raw.get("maximumAvailable")),
            currently_available=_to_decimal(throttle_raw.get("currentlyAvailable")),
            restore_rate=_to_decimal(throttle_raw.get("restoreRate")),
        )
    return QueryCost(
        requested=_to_decimal(cost.get("requestedQueryCost")),
        actual=_to_decimal(cost.get("actualQueryCost")),
        throttle=throttle,
    )


def _error_codes(errors: object) -> set[str]:
    codes: set[str] = set()
    if not isinstance(errors, list):
        return codes
    for item in errors:
        if not isinstance(item, Mapping):
            continue
        extensions = item.get("extensions")
        if isinstance(extensions, Mapping):
            code = extensions.get("code")
            if isinstance(code, str):
                codes.add(code.upper())
    return codes


def _safe_errors(errors: object) -> list[dict[str, Any]]:
    """Reduce Shopify's error list to the fields that are safe to keep.

    Two shapes share this helper because both are "an error Shopify reported":

    * **top-level** ``errors[]`` carry ``message``, ``path`` and
      ``extensions.code``;
    * **mutation** ``userErrors[]`` carry ``message``, ``field`` and a
      documented ``code`` at the top level of the entry.

    All of those name *what was wrong with the request shape*. Everything else
    in an entry can echo submitted values back — Shopify happily quotes an
    offending title or address — so it is dropped rather than forwarded into an
    error envelope a merchant might see or a log an operator might page through.
    """
    safe: list[dict[str, Any]] = []
    if not isinstance(errors, list):
        return safe
    for item in errors:
        if not isinstance(item, Mapping):
            continue
        entry: dict[str, Any] = {}
        message = item.get("message")
        if isinstance(message, str):
            entry["message"] = message[:300]
        path = item.get("path")
        if isinstance(path, list):
            entry["path"] = [str(part)[:80] for part in path[:10]]
        # `userErrors[].field` — the input path Shopify refused.
        field_path = item.get("field")
        if isinstance(field_path, list):
            entry["field"] = [str(part)[:80] for part in field_path[:10]]
        elif isinstance(field_path, str):
            entry["field"] = field_path[:80]
        # `code` sits at the entry root on userErrors and under `extensions` on
        # top-level errors. Both are documented and both are safe.
        code = item.get("code")
        if isinstance(code, str):
            entry["code"] = code
        extensions = item.get("extensions")
        if isinstance(extensions, Mapping):
            extension_code = extensions.get("code")
            if isinstance(extension_code, str):
                entry["code"] = extension_code
        if entry:
            safe.append(entry)
    return safe[:10]


def retry_after_seconds(header_value: str | None, *, now: float | None = None) -> float | None:
    """Interpret ``Retry-After`` in either permitted form.

    RFC 9110 allows delay-seconds or an HTTP-date, and Shopify's GraphQL
    documentation promises neither — so this is defensive rather than
    authoritative. A value in the past, unparseable, or absurdly large is
    discarded and the caller falls back to its own bounded backoff.
    """
    if not header_value:
        return None
    candidate = header_value.strip()
    try:
        seconds = float(candidate)
    except ValueError:
        try:
            deadline = parsedate_to_datetime(candidate)
        except (TypeError, ValueError):
            return None
        if deadline is None:
            return None
        seconds = deadline.timestamp() - (now if now is not None else time.time())
    if seconds <= 0:
        return None
    return min(seconds, _MAX_BACKOFF_SECONDS)


def cost_recovery_seconds(cost: QueryCost) -> float | None:
    """How long until the bucket holds enough points for the same query again.

    ``(requested - currentlyAvailable) / restoreRate``, which is the figure
    Shopify's own guidance implies. Returns ``None`` when any input is missing
    or nonsensical so the caller uses plain backoff instead of arithmetic on
    values it does not have.
    """
    requested = cost.requested
    available = cost.throttle.currently_available
    restore = cost.throttle.restore_rate
    if requested is None or available is None or restore is None or restore <= 0:
        return None
    deficit = requested - available
    if deficit <= 0:
        return None
    return min(float(deficit / restore), _MAX_BACKOFF_SECONDS)


def backoff_seconds(attempt: int, *, base: float = 1.0, jitter: float = 0.25) -> float:
    """Bounded exponential backoff with proportional jitter.

    Jitter is a fraction of the computed delay rather than a fixed spread, so
    the bound stays provable: the result is always within ``[delay*(1-jitter),
    delay*(1+jitter)]`` and never above ``_MAX_BACKOFF_SECONDS``. Tests assert
    that range rather than a value, which is the only way to test a random
    number without pinning the seed.
    """
    delay = min(base * (2**attempt), _MAX_BACKOFF_SECONDS)
    spread = delay * jitter
    jittered: float = delay + random.uniform(-spread, spread)  # noqa: S311 - backoff, not crypto
    bounded: float = min(jittered, _MAX_BACKOFF_SECONDS)
    return max(0.0, bounded)


def user_errors(payload: object, *, mutation_field: str) -> list[dict[str, Any]]:
    """Extract ``userErrors`` from one mutation's payload.

    **The caller names the field.** Shopify puts the payload under the mutation's
    own name (``productSet``, ``webhookSubscriptionCreate``, ...) and each has
    its own error type, so a client that went hunting for "something that looks
    like userErrors" would eventually find the wrong list — or miss one and
    report a refused mutation as success. Naming the field makes that a compile-
    time-ish decision at the operation layer instead of a runtime guess here.
    """
    if not isinstance(payload, Mapping):
        raise ShopifyGraphQLError("Shopify mutation payload was not an object.")
    field_payload = payload.get(mutation_field)
    if field_payload is None:
        raise ShopifyGraphQLError(
            f"Shopify mutation payload is missing the '{mutation_field}' field."
        )
    if not isinstance(field_payload, Mapping):
        raise ShopifyGraphQLError(f"Shopify mutation field '{mutation_field}' was not an object.")
    raw = field_payload.get("userErrors")
    if raw is None:
        # Every Admin mutation payload declares userErrors as non-null. Its
        # absence means the document did not request it, which is an authoring
        # bug that would otherwise make failures invisible.
        raise ShopifyGraphQLError(
            f"Mutation '{mutation_field}' did not select userErrors; "
            "every mutation document must request them."
        )
    return _safe_errors(raw)


def raise_for_user_errors(payload: object, *, mutation_field: str) -> None:
    """Fail unless the mutation reported no ``userErrors``."""
    errors = user_errors(payload, mutation_field=mutation_field)
    if errors:
        raise ShopifyUserError(
            "Shopify rejected the requested change.",
            details={"mutation": mutation_field, "userErrors": errors},
        )


class _SharedTransport:
    """One pooled ``AsyncClient`` for the process, carrying no credentials.

    The pool is worth sharing — TLS handshakes to Shopify are not free — but the
    *token* is not, so this client is created with no auth headers and every
    request supplies its own. That is the difference between reusing a
    connection pool and accidentally building a global object holding one
    merchant's credentials.

    Created lazily and closed from the application lifespan, matching how the
    rest of the process treats connection pools.
    """

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._lock = asyncio.Lock()

    async def get(self) -> httpx.AsyncClient:
        if self._client is not None and not self._client.is_closed:
            return self._client
        async with self._lock:
            if self._client is None or self._client.is_closed:
                config = settings.shopify
                self._client = httpx.AsyncClient(
                    timeout=httpx.Timeout(
                        config.request_timeout_seconds,
                        connect=config.connect_timeout_seconds,
                        read=config.request_timeout_seconds,
                        write=config.request_timeout_seconds,
                        pool=config.connect_timeout_seconds,
                    ),
                    limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
                    # Shopify never legitimately redirects this endpoint, and
                    # following one would send the access token to wherever the
                    # Location header pointed.
                    follow_redirects=False,
                )
            return self._client

    async def aclose(self) -> None:
        client, self._client = self._client, None
        if client is not None and not client.is_closed:
            await client.aclose()


_transport = _SharedTransport()


async def close_shopify_graphql_client() -> None:
    """Close the shared pool. Called from the application lifespan."""
    await _transport.aclose()


class ShopifyGraphQLClient:
    """Executes one shop's GraphQL operations.

    Constructed per shop with an already-decrypted token from the existing
    connection service. It holds the token only for its own lifetime and never
    writes it anywhere — see ``__repr__``.
    """

    __slots__ = ("_api_version", "_max_attempts", "_shop_domain", "_token", "_transport_override")

    def __init__(
        self,
        *,
        shop_domain: str,
        access_token: str,
        api_version: str | None = None,
        max_attempts: int | None = None,
        transport: httpx.AsyncClient | None = None,
    ) -> None:
        if not is_canonical_shop_domain(shop_domain):
            # Deliberately does not normalise. Normalisation belongs at the
            # OAuth boundary where a human typed something; by the time a token
            # exists the domain is canonical, and "helpfully" repairing it here
            # would mean an attacker-supplied string gets a second chance.
            raise ShopifyInvalidShopError(
                "The Shopify GraphQL client requires a canonical *.myshopify.com domain."
            )
        if not access_token or not access_token.strip():
            raise ShopifyAuthError()
        self._shop_domain = shop_domain
        self._token = access_token
        self._api_version = api_version or settings.shopify.graphql_api_version
        self._max_attempts = max_attempts if max_attempts is not None else 3
        self._transport_override = transport

    def __repr__(self) -> str:
        # No token, no headers. A client object reaching a log or a traceback
        # frame must not be the thing that leaks a merchant's credentials.
        return (
            f"ShopifyGraphQLClient(shop_domain={self._shop_domain!r}, "
            f"api_version={self._api_version!r})"
        )

    __str__ = __repr__

    @property
    def endpoint(self) -> str:
        """The full endpoint, built here and never accepted from a caller."""
        return f"https://{self._shop_domain}/admin/api/{self._api_version}/graphql.json"

    @property
    def api_version(self) -> str:
        return self._api_version

    async def _client(self) -> httpx.AsyncClient:
        if self._transport_override is not None:
            return self._transport_override
        return await _transport.get()

    async def execute(
        self,
        *,
        document: str,
        operation_name: str | None = None,
        operation_type: OperationType | None = None,
        variables: Mapping[str, Any] | None = None,
        allow_partial_data: bool = False,
    ) -> GraphQLResponse:
        """Run one named operation.

        ``document`` is a static string owned by this codebase. Merchant and shop
        values travel in ``variables`` and nowhere else — interpolating them into
        the document is how a GraphQL injection happens, and it also defeats
        Shopify's query-cost caching.

        ``operation_name`` may be omitted when the document defines exactly one
        operation. ``operation_type``, if given, is an **assertion** checked
        against the parsed document -- it decides nothing on its own.

        ``allow_partial_data`` exists so the option is explicit and greppable. It
        defaults to closed and is unused on every GQL-1 path: a response that
        carries both ``data`` and ``errors`` is a partial failure, and treating
        it as success is how half a catalogue silently goes unsynced.
        """
        # Parse first. Nothing goes on the wire until the document is known to
        # be valid and the operation is known to be one this client may send --
        # and, critically, until its *kind* is known from the document rather
        # than from what the caller said about it.
        selected = select_operation(document, operation_name)
        assert_declaration_matches(selected, operation_type)

        body: dict[str, Any] = {
            "query": document,
            "variables": dict(variables or {}),
        }
        if selected.name is not None:
            # Omitted for an anonymous operation: sending a name Shopify cannot
            # match in the document is an error, not a courtesy.
            body["operationName"] = selected.name
        headers = {
            "X-Shopify-Access-Token": self._token,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        # Mutations never retry automatically: replay safety is a
        # per-operation decision (does Shopify dedupe it? is there an
        # idempotency key?) and the operation layer is where that is known.
        #
        # `selected` -- the parsed document -- is the authority. A caller
        # cannot buy retries for a mutation by claiming it is a query.
        attempts = max(1, self._max_attempts) if selected.is_retryable_kind else 1
        client = await self._client()
        started = time.monotonic()
        waited = 0.0
        last_error: ShopifyError | None = None

        for attempt in range(attempts):
            try:
                response = await client.post(self.endpoint, headers=headers, json=body)
            except httpx.TimeoutException as exc:
                last_error = ShopifyTimeoutError()
                if attempt + 1 >= attempts:
                    raise last_error from exc
                waited += await self._sleep(backoff_seconds(attempt), waited)
                continue
            except httpx.HTTPError as exc:
                # Transport failure with no response: DNS, TLS, connection reset.
                # str(exc) can carry the URL but never a header, so it is safe.
                last_error = ShopifyError(f"Shopify GraphQL transport failure: {exc}")
                if attempt + 1 >= attempts:
                    raise last_error from exc
                waited += await self._sleep(backoff_seconds(attempt), waited)
                continue

            request_id = response.headers.get(REQUEST_ID_HEADER)

            if response.status_code == 429:
                last_error = ShopifyThrottledError(request_id=request_id)
                if attempt + 1 >= attempts:
                    raise last_error
                delay = retry_after_seconds(response.headers.get("Retry-After"))
                waited += await self._sleep(delay or backoff_seconds(attempt), waited)
                continue

            if response.status_code == 401:
                raise ShopifyAuthError()
            if response.status_code == 403:
                # Distinguished from 401 in the log; the merchant-facing error
                # stays uniform because both mean "reconnect the store".
                self._log("shopify_graphql_forbidden", selected.name, request_id, 403, attempt)
                raise ShopifyAuthError()

            if response.status_code in _RETRYABLE_STATUSES:
                last_error = ShopifyResponseError(
                    f"Shopify GraphQL returned HTTP {response.status_code}",
                    upstream_code=str(response.status_code),
                )
                if attempt + 1 >= attempts:
                    raise last_error
                waited += await self._sleep(backoff_seconds(attempt), waited)
                continue

            if response.status_code >= 400:
                raise ShopifyResponseError(
                    f"Shopify GraphQL returned HTTP {response.status_code}",
                    upstream_code=str(response.status_code),
                )

            return self._parse(
                response=response,
                operation_name=selected.name,
                request_id=request_id,
                attempt=attempt,
                allow_partial_data=allow_partial_data,
                elapsed=time.monotonic() - started,
            )

        raise last_error or ShopifyError("Shopify GraphQL request failed.")

    async def _sleep(self, delay: float, already_waited: float) -> float:
        """Sleep, respecting the total-wait ceiling.

        Returns what was actually slept so the caller can accumulate it. The cap
        is what stops a long chain of individually-bounded waits from adding up
        to an unbounded one.
        """
        remaining = _MAX_TOTAL_WAIT_SECONDS - already_waited
        if remaining <= 0:
            return 0.0
        actual = min(delay, remaining)
        # A plain await: cancellation propagates, so a shutting-down worker is
        # not held open by a retry sleep.
        await asyncio.sleep(actual)
        return actual

    def _parse(
        self,
        *,
        response: httpx.Response,
        operation_name: str | None,
        request_id: str | None,
        attempt: int,
        allow_partial_data: bool,
        elapsed: float,
    ) -> GraphQLResponse:
        try:
            # parse_float=Decimal for the same reason the REST client does it:
            # money must never round-trip through a binary float.
            payload = json.loads(response.text, parse_float=Decimal)
        except ValueError as exc:
            raise ShopifyResponseError(
                "Shopify GraphQL returned a non-JSON response.",
                upstream_code=str(response.status_code),
            ) from exc
        if not isinstance(payload, dict):
            raise ShopifyResponseError("Shopify GraphQL response was not a JSON object.")

        cost = parse_cost(payload.get("extensions"))
        errors = payload.get("errors")

        if errors:
            codes = _error_codes(errors)
            safe = _safe_errors(errors)
            self._log(
                "shopify_graphql_errors",
                operation_name,
                request_id,
                response.status_code,
                attempt,
                cost=cost,
                elapsed=elapsed,
                error_codes=sorted(codes),
            )
            if THROTTLED_CODE in codes:
                raise ShopifyThrottledError(
                    details={"errors": safe, "cost": _cost_log(cost)},
                    upstream_code=THROTTLED_CODE,
                    request_id=request_id,
                )
            if MAX_COST_CODE in codes:
                raise ShopifyQueryCostError(
                    details={"errors": safe},
                    upstream_code=MAX_COST_CODE,
                    request_id=request_id,
                )
            if ACCESS_DENIED_CODE in codes:
                raise ShopifyAuthError()
            # Fail closed. `data` may be populated alongside errors; acting on it
            # would report a partial sync as a complete one.
            if not allow_partial_data:
                raise ShopifyGraphQLError(
                    "Shopify GraphQL returned errors.",
                    details={"errors": safe},
                    request_id=request_id,
                )

        data = payload.get("data")
        if not isinstance(data, dict):
            raise ShopifyGraphQLError(
                "Shopify GraphQL response contained no data object.",
                details={"errors": _safe_errors(errors)} if errors else None,
                request_id=request_id,
            )

        self._log(
            "shopify_graphql_ok",
            operation_name,
            request_id,
            response.status_code,
            attempt,
            cost=cost,
            elapsed=elapsed,
        )
        return GraphQLResponse(
            data=data,
            operation_name=operation_name,
            api_version=self._api_version,
            request_id=request_id,
            cost=cost,
            attempts=attempt + 1,
        )

    def _log(
        self,
        event: str,
        operation_name: str | None,
        request_id: str | None,
        status: int,
        attempt: int,
        *,
        cost: QueryCost | None = None,
        elapsed: float | None = None,
        error_codes: list[str] | None = None,
    ) -> None:
        """Structured, and deliberately narrow.

        Operation name, shop, version, timing and cost — the things an operator
        needs to answer "which call, how expensive, how close to the limit".
        Never variables (merchant data), never the token, never the response.
        """
        fields: dict[str, Any] = {
            "operation_name": operation_name,
            "shop_domain": self._shop_domain,
            "api_version": self._api_version,
            "attempt": attempt + 1,
            "status": status,
        }
        if request_id:
            fields["shopify_request_id"] = request_id
        if elapsed is not None:
            fields["duration_ms"] = round(elapsed * 1000, 2)
        if cost is not None:
            fields.update(_cost_log(cost))
        if error_codes:
            fields["error_codes"] = error_codes
        logger.info(event, **fields)


def _cost_log(cost: QueryCost) -> dict[str, Any]:
    return {
        "requested_cost": str(cost.requested) if cost.requested is not None else None,
        "actual_cost": str(cost.actual) if cost.actual is not None else None,
        "throttle_maximum": (
            str(cost.throttle.maximum_available)
            if cost.throttle.maximum_available is not None
            else None
        ),
        "throttle_available": (
            str(cost.throttle.currently_available)
            if cost.throttle.currently_available is not None
            else None
        ),
        "throttle_restore_rate": (
            str(cost.throttle.restore_rate) if cost.throttle.restore_rate is not None else None
        ),
    }


__all__ = [
    "GraphQLResponse",
    # Re-exported from `operations` so existing imports keep working; the enum
    # is defined there now because that is where it is interpreted.
    "OperationType",
    "QueryCost",
    "SelectedOperation",
    "ShopifyGraphQLClient",
    "ThrottleStatus",
    "backoff_seconds",
    "close_shopify_graphql_client",
    "cost_recovery_seconds",
    "parse_cost",
    "raise_for_user_errors",
    "retry_after_seconds",
    "select_operation",
    "user_errors",
]
