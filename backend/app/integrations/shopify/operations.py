"""Which GraphQL operation is being sent, decided by parsing the document.

Retry eligibility hangs on this. A query may be retried on a timeout; a mutation
may not, because a timed-out ``productCreate`` that succeeded server-side would
create a second product on the next attempt.

The first version of this client took the answer from the caller as an
``OperationType`` argument. That is an assertion, not a fact, and a mutation
declared as a query bought three attempts at it. So the document is parsed and
the *parsed* operation decides; the declaration, if supplied, is checked against
it and a disagreement is a local error.

**Parsed with ``graphql-core``**, the reference Python implementation of the
spec. A regex or substring check is not an option here and never was: the word
``mutation`` appears in comments, in fragment names and in field names, a
document can hold several operations of different kinds, and the failure mode of
guessing wrong is silent duplicate writes to a merchant's catalogue.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from graphql import GraphQLSyntaxError, parse
from graphql.language.ast import OperationDefinitionNode
from graphql.language.ast import OperationType as GraphQLOperationType

from app.integrations.shopify.exceptions import ShopifyOperationError


class OperationType(StrEnum):
    """The kind of operation being executed.

    Still accepted from callers, but only as an **assertion** that is checked
    against the parsed document. It is no longer the authority for anything.
    """

    QUERY = "query"
    MUTATION = "mutation"


@dataclass(frozen=True, slots=True)
class SelectedOperation:
    """The one operation this request will execute.

    ``name`` is ``None`` for an anonymous operation, which GraphQL permits when
    a document holds exactly one. That ``None`` is meaningful: it is what stops
    the client sending an ``operationName`` Shopify would then fail to match.
    """

    name: str | None
    operation_type: OperationType

    @property
    def is_retryable_kind(self) -> bool:
        """Whether this *kind* of operation may be retried at all.

        The transport still decides whether a given failure is transient. This
        only answers the prior question: is replaying it safe in principle.
        """
        return self.operation_type is OperationType.QUERY


def select_operation(document: str, operation_name: str | None) -> SelectedOperation:
    """Parse the document and resolve which operation runs.

    Raises :class:`ShopifyOperationError` for anything that cannot be resolved,
    always **before** a request is made. Sending a document that does not parse,
    or whose named operation does not exist, buys nothing but a round trip and a
    Shopify-side error that is harder to read than this one.
    """
    if not document or not document.strip():
        raise ShopifyOperationError("A GraphQL document is required.")

    # A supplied-but-blank name is not the same as omitting it. Omitting is a
    # valid GraphQL choice; supplying whitespace is a bug at the call site.
    if operation_name is not None and not operation_name.strip():
        raise ShopifyOperationError("A GraphQL operation name cannot be blank.")

    try:
        ast = parse(document)
    except GraphQLSyntaxError as exc:
        # `exc.message` describes the syntax problem and quotes only the
        # document, which this codebase owns -- never merchant data, which
        # travels in variables.
        raise ShopifyOperationError(
            f"The GraphQL document could not be parsed: {exc.message}"
        ) from exc

    operations = [node for node in ast.definitions if isinstance(node, OperationDefinitionNode)]
    if not operations:
        raise ShopifyOperationError(
            "The GraphQL document contains no operation to execute; "
            "a document of fragments alone cannot be sent."
        )

    if operation_name is None:
        if len(operations) > 1:
            names = sorted(node.name.value for node in operations if node.name)
            raise ShopifyOperationError(
                "The GraphQL document defines more than one operation, so an "
                f"operation name is required. Available: {names or ['<anonymous>']}."
            )
        selected = operations[0]
    else:
        wanted = operation_name.strip()
        matches = [node for node in operations if node.name and node.name.value == wanted]
        if not matches:
            available = sorted(node.name.value for node in operations if node.name)
            raise ShopifyOperationError(
                f"The GraphQL document defines no operation named {wanted!r}. "
                f"Available: {available or ['<anonymous>']}."
            )
        if len(matches) > 1:
            # graphql-core parses this happily; the server would reject it, and
            # which one "wins" would decide retry eligibility.
            raise ShopifyOperationError(f"The GraphQL document defines {wanted!r} more than once.")
        selected = matches[0]

    return SelectedOperation(
        name=selected.name.value if selected.name else None,
        operation_type=_map_operation_type(selected.operation),
    )


def _map_operation_type(operation: GraphQLOperationType) -> OperationType:
    if operation is GraphQLOperationType.QUERY:
        return OperationType.QUERY
    if operation is GraphQLOperationType.MUTATION:
        return OperationType.MUTATION
    # Subscriptions are refused rather than passed through. The Admin API this
    # client talks to is request/response, and a subscription's retry and
    # lifetime semantics are not something this transport models -- silently
    # treating one as a query would give it query retries.
    raise ShopifyOperationError(
        f"GraphQL {operation.value} operations are not supported by this client."
    )


def assert_declaration_matches(selected: SelectedOperation, declared: OperationType | None) -> None:
    """Check a caller's declaration against the parsed truth.

    Both directions are refused. Declaring a mutation as a query is the
    dangerous one — it is what made a mutation retryable — but the reverse is
    equally a false statement about the document, and a mismatch that is
    tolerated in one direction is a mismatch that gets trusted in the other.
    """
    if declared is None or declared is selected.operation_type:
        return
    raise ShopifyOperationError(
        f"The document defines a {selected.operation_type.value}, but the caller "
        f"declared a {declared.value}. Retry eligibility is decided by the "
        "document, so the declaration must match it."
    )


__all__ = [
    "OperationType",
    "SelectedOperation",
    "assert_declaration_matches",
    "select_operation",
]
