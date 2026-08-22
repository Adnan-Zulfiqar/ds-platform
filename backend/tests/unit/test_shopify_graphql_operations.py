"""GQL-1 acceptance fix F-02 — retry eligibility comes from the document.

The original client trusted the caller's ``OperationType``. That is an assertion,
not a fact, and it decided whether a failed call was retried: a mutation declared
as a query became eligible for automatic retry, so a timed-out
``productCreate`` could run twice and leave the merchant with two products.

These tests are about **behaviour under a lie**. The security invariant asserted
throughout is the one that matters: no mutation document can become retryable
through caller metadata, and a mislabelled document never reaches the network at
all.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.integrations.shopify.exceptions import (
    ShopifyOperationError,
    ShopifyTimeoutError,
)
from app.integrations.shopify.graphql import OperationType, ShopifyGraphQLClient

pytestmark = pytest.mark.unit

SHOP = "demo-shop.myshopify.com"
TOKEN = "shpat_TEST_TOKEN_NEVER_REAL"

MUTATION_DOC = """
mutation CreateProduct($input: ProductInput!) {
  productCreate(input: $input) {
    product { id }
    userErrors { field message }
  }
}
""".strip()

QUERY_DOC = "query ShopName { shop { name } }"


class Recorder:
    """Counts real HTTP attempts. The only thing these tests trust."""

    def __init__(self, response: httpx.Response | None = None, *, raise_timeout: bool = False):
        self.attempts = 0
        self._response = response
        self._raise_timeout = raise_timeout

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.attempts += 1
        if self._raise_timeout:
            raise httpx.ReadTimeout("slow", request=request)
        return self._response or httpx.Response(200, json={"data": {"ok": True}})


def build_client(recorder: Recorder, *, max_attempts: int = 3) -> ShopifyGraphQLClient:
    return ShopifyGraphQLClient(
        shop_domain=SHOP,
        access_token=TOKEN,
        max_attempts=max_attempts,
        transport=httpx.AsyncClient(transport=httpx.MockTransport(recorder)),
    )


async def execute(client: ShopifyGraphQLClient, **kwargs: Any) -> Any:
    return await client.execute(
        document=kwargs.pop("document", QUERY_DOC),
        operation_name=kwargs.pop("operation_name", None),
        operation_type=kwargs.pop("operation_type", None),
        **kwargs,
    )


# ------------------------------------------------------ the security invariant
class TestAMutationCanNeverBeMadeRetryable:
    async def test_a_mutation_declared_as_a_query_is_refused_before_the_network(self) -> None:
        """F-02, stated as the invariant.

        Declaring `QUERY` over a mutation document used to buy three attempts at
        a `productCreate`. It now fails locally: the parsed operation is the
        authority, the declaration is only an assertion, and a disagreement is
        an error rather than a silent downgrade.
        """
        recorder = Recorder(raise_timeout=True)
        client = build_client(recorder)

        with pytest.raises(ShopifyOperationError) as raised:
            await execute(
                client,
                document=MUTATION_DOC,
                operation_name="CreateProduct",
                operation_type=OperationType.QUERY,
            )

        assert recorder.attempts == 0, "a mislabelled mutation reached the network"
        assert "mutation" in str(raised.value).lower()

    async def test_a_mutation_never_retries_even_when_correctly_declared(self) -> None:
        recorder = Recorder(raise_timeout=True)
        client = build_client(recorder, max_attempts=5)

        with pytest.raises(ShopifyTimeoutError):
            await execute(
                client,
                document=MUTATION_DOC,
                operation_name="CreateProduct",
                operation_type=OperationType.MUTATION,
            )

        assert recorder.attempts == 1

    async def test_a_mutation_never_retries_when_no_type_is_declared_at_all(self) -> None:
        """The parser is the authority; omitting the declaration changes nothing."""
        recorder = Recorder(raise_timeout=True)
        client = build_client(recorder, max_attempts=5)

        with pytest.raises(ShopifyTimeoutError):
            await execute(client, document=MUTATION_DOC, operation_name="CreateProduct")

        assert recorder.attempts == 1

    @pytest.mark.parametrize("status", [429, 502, 503, 504])
    async def test_a_mutation_never_retries_a_transient_http_status(
        self, status: int, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import asyncio

        async def no_sleep(_seconds: float) -> None:
            return None

        monkeypatch.setattr(asyncio, "sleep", no_sleep)
        recorder = Recorder(httpx.Response(status, json={}))
        client = build_client(recorder, max_attempts=5)

        with pytest.raises(Exception):  # noqa: B017 - the type varies by status
            await execute(
                client,
                document=MUTATION_DOC,
                operation_name="CreateProduct",
                operation_type=OperationType.MUTATION,
            )

        assert recorder.attempts == 1

    async def test_a_query_keeps_its_bounded_transient_retries(self) -> None:
        recorder = Recorder(raise_timeout=True)
        client = build_client(recorder, max_attempts=3)

        with pytest.raises(ShopifyTimeoutError):
            await execute(client, document=QUERY_DOC, operation_name="ShopName")

        assert recorder.attempts == 3


# ---------------------------------------------------------- operation selection
class TestOperationSelection:
    async def test_a_named_query_is_selected(self) -> None:
        recorder = Recorder()
        result = await execute(build_client(recorder), operation_name="ShopName")
        assert result.operation_name == "ShopName"
        assert recorder.attempts == 1

    async def test_an_anonymous_single_query_needs_no_operation_name(self) -> None:
        """GraphQL permits omitting `operationName` when the document holds one
        operation, and Shopify accepts it."""
        recorder = Recorder()
        result = await execute(build_client(recorder), document="{ shop { name } }")
        assert recorder.attempts == 1
        assert result.operation_name is None or result.operation_name == ""

    async def test_a_named_mutation_is_selected(self) -> None:
        recorder = Recorder()
        result = await execute(
            build_client(recorder), document=MUTATION_DOC, operation_name="CreateProduct"
        )
        assert result.operation_name == "CreateProduct"

    async def test_leading_comments_and_whitespace_are_handled(self) -> None:
        document = """
        # A leading comment mentioning the word mutation, which a regex
        # detector would happily trip over.

        query ShopName { shop { name } }
        """
        recorder = Recorder(raise_timeout=True)
        client = build_client(recorder, max_attempts=2)

        with pytest.raises(ShopifyTimeoutError):
            await execute(client, document=document, operation_name="ShopName")

        assert recorder.attempts == 2, "a commented query must still be a retryable query"

    async def test_a_fragment_before_a_query_is_handled(self) -> None:
        document = """
        fragment ShopFields on Shop { name currencyCode }
        query ShopName { shop { ...ShopFields } }
        """
        recorder = Recorder()
        await execute(build_client(recorder), document=document, operation_name="ShopName")
        assert recorder.attempts == 1

    async def test_a_fragment_before_a_mutation_is_still_a_mutation(self) -> None:
        """The case a substring detector gets wrong in the dangerous direction."""
        document = """
        fragment E on UserError { field message }
        mutation CreateProduct($input: ProductInput!) {
          productCreate(input: $input) { userErrors { ...E } }
        }
        """
        recorder = Recorder(raise_timeout=True)
        client = build_client(recorder, max_attempts=5)

        with pytest.raises(ShopifyTimeoutError):
            await execute(client, document=document, operation_name="CreateProduct")

        assert recorder.attempts == 1

    async def test_multiple_operations_select_the_named_one(self) -> None:
        document = """
        query ShopName { shop { name } }
        mutation CreateProduct($input: ProductInput!) {
          productCreate(input: $input) { product { id } }
        }
        """
        recorder = Recorder(raise_timeout=True)

        # Selecting the query half gets query retries...
        with pytest.raises(ShopifyTimeoutError):
            await execute(
                build_client(recorder, max_attempts=3),
                document=document,
                operation_name="ShopName",
            )
        assert recorder.attempts == 3

        # ...and selecting the mutation half gets none, from the same document.
        second = Recorder(raise_timeout=True)
        with pytest.raises(ShopifyTimeoutError):
            await execute(
                build_client(second, max_attempts=3),
                document=document,
                operation_name="CreateProduct",
            )
        assert second.attempts == 1

    async def test_multiple_operations_without_a_name_are_refused(self) -> None:
        document = """
        query A { shop { name } }
        query B { shop { id } }
        """
        recorder = Recorder()
        with pytest.raises(ShopifyOperationError):
            await execute(build_client(recorder), document=document)
        assert recorder.attempts == 0

    async def test_an_unknown_operation_name_is_refused(self) -> None:
        recorder = Recorder()
        with pytest.raises(ShopifyOperationError):
            await execute(build_client(recorder), operation_name="NoSuchOperation")
        assert recorder.attempts == 0

    async def test_a_malformed_document_fails_locally(self) -> None:
        recorder = Recorder()
        with pytest.raises(ShopifyOperationError):
            await execute(build_client(recorder), document="query ShopName { shop { name ")
        assert recorder.attempts == 0, "a malformed document was sent to Shopify"

    async def test_a_fragments_only_document_fails_locally(self) -> None:
        recorder = Recorder()
        with pytest.raises(ShopifyOperationError):
            await execute(build_client(recorder), document="fragment F on Shop { name }")
        assert recorder.attempts == 0

    async def test_a_subscription_is_refused(self) -> None:
        """Not supported: the Admin API is request/response here, and a
        subscription's retry semantics are not something this client models."""
        recorder = Recorder()
        with pytest.raises(ShopifyOperationError):
            await execute(
                build_client(recorder),
                document="subscription S { shopUpdated { id } }",
                operation_name="S",
            )
        assert recorder.attempts == 0

    async def test_a_query_declared_as_a_mutation_is_also_refused(self) -> None:
        """The harmless direction is still a lie about the document, and a lie
        that is tolerated in one direction gets trusted in the other."""
        recorder = Recorder()
        with pytest.raises(ShopifyOperationError):
            await execute(
                build_client(recorder),
                document=QUERY_DOC,
                operation_name="ShopName",
                operation_type=OperationType.MUTATION,
            )
        assert recorder.attempts == 0


class TestPayloadMatchesTheSelectedOperation:
    async def test_the_operation_name_sent_is_the_one_selected(self) -> None:
        import json

        document = """
        query ShopName { shop { name } }
        query ShopId { shop { id } }
        """
        seen: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(json.loads(request.content))
            return httpx.Response(200, json={"data": {"shop": {"id": "1"}}})

        client = ShopifyGraphQLClient(
            shop_domain=SHOP,
            access_token=TOKEN,
            transport=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )
        await client.execute(document=document, operation_name="ShopId")

        assert seen["operationName"] == "ShopId"
        assert seen["query"] == document

    async def test_an_anonymous_operation_sends_no_operation_name(self) -> None:
        import json

        seen: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(json.loads(request.content))
            return httpx.Response(200, json={"data": {"shop": {"name": "Demo"}}})

        client = ShopifyGraphQLClient(
            shop_domain=SHOP,
            access_token=TOKEN,
            transport=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )
        await client.execute(document="{ shop { name } }")

        assert seen.get("operationName") is None
